"""The policy/value network (DESIGN.md 5), small enough to train on a CPU.

A transformer over 13 tokens: the field, then 12 Pokemon (the observer's six,
then the opponent's six). Each Pokemon token embeds its dex ids (species,
ability, item, its four moves in order, two types) next to its features.

Heads:
- value: the observer's expected result, in [-1, 1];
- slots: 47 logits for each active slot, read from that slot's own Pokemon
  token. A joint action's logit is the sum of its two slot logits, masked to
  the legal joint actions;
- preview: an ordered pick of four is scored as lead scores for the first
  two plus bring scores for the other two, per Pokemon token.
"""
from __future__ import annotations

import itertools

import numpy as np
import torch
from torch import nn

from selfplay.env import SIZES

SLOT = SIZES["slot_actions"]
TOKENS = SIZES["tokens"]
# Feature columns of a Pokemon token (engine/src/env/obs.rs).
ACTIVE_SLOT0, ACTIVE_SLOT1 = 24, 25


def preview_orders() -> np.ndarray:
    """The 180 ordered picks, in the engine's index order (team.rs)."""
    out = []
    for a, b in itertools.permutations(range(6), 2):
        rest = [x for x in range(6) if x not in (a, b)]
        for i, j in itertools.combinations(range(len(rest)), 2):
            out.append((a, b, rest[i], rest[j]))
    return np.array(out, dtype=np.int64)


class PolicyNet(nn.Module):
    def __init__(self, d: int = 64, layers: int = 2, heads: int = 4):
        super().__init__()
        s = SIZES
        self.species = nn.Embedding(s["vocab_species"], 24, padding_idx=0)
        self.ability = nn.Embedding(s["vocab_abilities"], 12, padding_idx=0)
        self.item = nn.Embedding(s["vocab_items"], 12, padding_idx=0)
        self.move = nn.Embedding(s["vocab_moves"], 12, padding_idx=0)
        self.type_emb = nn.Embedding(s["vocab_types"], 6, padding_idx=0)
        mon_in = 24 + 12 + 12 + 4 * 12 + 2 * 6 + s["mon_floats"]
        self.mon_proj = nn.Sequential(nn.Linear(mon_in, d), nn.GELU(), nn.Linear(d, d))
        self.field_proj = nn.Sequential(nn.Linear(s["field_floats"], d), nn.GELU(), nn.Linear(d, d))
        # Which token: field, own 0-5, foe 0-5.
        self.position = nn.Parameter(torch.zeros(TOKENS + 1, d))
        layer = nn.TransformerEncoderLayer(d, heads, 2 * d, dropout=0.0, batch_first=True,
                                           norm_first=True, activation="gelu")
        self.encoder = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(d)
        self.value_head = nn.Sequential(nn.Linear(d, d), nn.GELU(), nn.Linear(d, 1))
        self.slot_head = nn.Sequential(nn.Linear(2 * d, d), nn.GELU(), nn.Linear(d, SLOT))
        self.preview_head = nn.Sequential(nn.Linear(2 * d, d), nn.GELU(), nn.Linear(d, 2))
        self.register_buffer("orders", torch.from_numpy(preview_orders()), persistent=False)

    def encode(self, ints: torch.Tensor, mons: torch.Tensor, field: torch.Tensor) -> torch.Tensor:
        """[B, 13, d] token states."""
        ids = ints.long()
        emb = torch.cat([
            self.species(ids[..., 0]),
            self.ability(ids[..., 1]),
            self.item(ids[..., 2]),
            self.move(ids[..., 3:7]).flatten(-2),
            self.type_emb(ids[..., 7:9]).flatten(-2),
            mons,
        ], dim=-1)
        x = torch.cat([self.field_proj(field).unsqueeze(1), self.mon_proj(emb)], dim=1)
        return self.norm(self.encoder(x + self.position))

    def forward(self, ints, mons, field, masks, decisions):
        """Masked log-probabilities over the mask's action space [B, mask_len]
        and values [B]. Rows with no decision get a uniform dummy distribution."""
        h = self.encode(ints, mons, field)
        g = h[:, 0]
        own = h[:, 1:7]
        value = torch.tanh(self.value_head(g)).squeeze(-1)

        # Moves and switches: each slot reads its active Pokemon's token.
        logits = torch.full(masks.shape, -1e9, dtype=h.dtype, device=h.device)
        slots = []
        for col in (ACTIVE_SLOT0, ACTIVE_SLOT1):
            w = mons[:, :6, col].unsqueeze(-1)                     # [B, 6, 1] one-hot
            tok = (own * w).sum(1)
            slots.append(self.slot_head(torch.cat([tok, g], -1)))  # [B, 47]
        joint = (slots[0].unsqueeze(2) + slots[1].unsqueeze(1)).flatten(1)  # [B, 2209]
        is_slots = (decisions == 2).unsqueeze(-1)
        logits = torch.where(is_slots, joint, logits)

        # Team preview: lead scores for the first two picks, bring for the rest.
        sc = self.preview_head(torch.cat([own, g.unsqueeze(1).expand_as(own)], -1))  # [B, 6, 2]
        o = self.orders
        lead, bring = sc[..., 0], sc[..., 1]
        prev = lead[:, o[:, 0]] + lead[:, o[:, 1]] + bring[:, o[:, 2]] + bring[:, o[:, 3]]
        n = prev.shape[1]
        is_prev = (decisions == 1).unsqueeze(-1)
        logits[:, :n] = torch.where(is_prev, prev, logits[:, :n])

        legal = masks.bool()
        logits = logits.masked_fill(~legal, -1e9)
        none = ~legal.any(-1, keepdim=True)
        logits = torch.where(none, torch.zeros_like(logits), logits)
        return torch.log_softmax(logits, -1), value
