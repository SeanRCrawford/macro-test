"""The policy/value network (DESIGN.md 5), small enough to train on a CPU.

A transformer over 13 tokens: the field, then 12 Pokemon (the observer's six,
then the opponent's six). Each Pokemon token embeds its dex ids (species,
ability, item, its four moves in order, two types) next to its features.

Heads:
- value: the observer's expected result, in [-1, 1];
- joint actions: every legal pair of slot actions gets its own logit. Each
  slot's 47 actions are embedded (the move with its target's token, PP and
  expected damage; the Pokemon a switch brings in; pass), and a pair scores
  u0(a) + u1(b) + <P e0(a), Q e1(b)>. The bilinear term lets the policy
  correlate the slots (a mix of "Protect + Tailwind" and "attack +
  Moonblast" without the crossed pairs), which a sum of per-slot logits
  can't;
- opponent: the same joint scoring for the opponent's actives, trained to
  predict the opponent's actual joint action (an auxiliary loss);
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
PP, DISABLED = 32, 36
DAMAGE = SIZES["damage_at"]
PARTY = SIZES["party_at"]
# Slot action layout (engine/src/env/action.rs): move (m*5+t)*2+mega for
# targets [0, 1, 2, -1, -2], then switch to party position 0-5, then pass.
N_MOVE, N_SWITCH = 40, 6


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
        self.actions = SlotActions(d, self.move)
        self.policy_head = JointHead(d)
        self.opponent_head = JointHead(d)
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

    def value(self, ints, mons, field):
        """The observer's expected result [B], in [-1, 1]."""
        g = self.encode(ints, mons, field)[:, 0]
        return torch.tanh(self.value_head(g)).squeeze(-1)

    def forward(self, ints, mons, field, masks, decisions, opp_masks=None):
        """Masked log-probabilities over the mask's action space [B, mask_len]
        and values [B]. Rows with no decision get a uniform dummy
        distribution. With `opp_masks`, also the predicted log-probabilities
        of the opponent's joint action [B, 2209] (meaningful where the
        opponent chooses moves)."""
        h = self.encode(ints, mons, field)
        g = h[:, 0]
        own = h[:, 1:7]
        value = torch.tanh(self.value_head(g)).squeeze(-1)

        logits = torch.full(masks.shape, -1e9, dtype=h.dtype, device=h.device)
        joint = self.policy_head(*self.actions(h, ints, mons, observer=True))
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

        logp = masked_log_softmax(logits, masks)
        if opp_masks is None:
            return logp, value
        opp = self.opponent_head(*self.actions(h, ints, mons, observer=False))
        return logp, value, masked_log_softmax(opp, opp_masks)


def masked_log_softmax(logits, masks):
    legal = masks.bool()
    logits = logits.masked_fill(~legal, -1e9)
    none = ~legal.any(-1, keepdim=True)
    logits = torch.where(none, torch.zeros_like(logits), logits)
    return torch.log_softmax(logits, -1)


def pick(x, w):
    """Sum over tokens of x [B, 6, ...] weighted by the one-hot w [B, 6]."""
    return (x * w.reshape(*w.shape, *([1] * (x.dim() - 2)))).sum(1)


class SlotActions(nn.Module):
    """Embeddings [B, 47, d] of each active slot's actions, for the observer's
    side or the opponent's."""

    def __init__(self, d: int, move_embedding: nn.Embedding):
        super().__init__()
        self.d = d
        self.move_embedding = move_embedding    # shared with PolicyNet
        move_in = 12 + 2 + 1 + d + 5 + 1     # move, pp, disabled, damage, target, target kind, mega
        self.move = nn.Linear(move_in, d)
        self.switch = nn.Linear(d, d)
        self.pass_ = nn.Parameter(torch.zeros(d))
        self.context = nn.Linear(2 * d, d)
        self.out = nn.Sequential(nn.GELU(), nn.Linear(d, d))
        self.register_buffer("target_kind", torch.eye(5), persistent=False)
        self.register_buffer("mega", torch.tensor([0.0, 1.0]), persistent=False)

    def forward(self, h, ints, mons, observer: bool):
        g = h[:, 0]
        mine, theirs = (slice(1, 7), slice(7, 13)) if observer else (slice(7, 13), slice(1, 7))
        mi, ti = (slice(0, 6), slice(6, 12)) if observer else (slice(6, 12), slice(0, 6))
        hm, ht = h[:, mine], h[:, theirs]
        fm, ft = mons[:, mi], mons[:, ti]
        B, d = h.shape[0], self.d
        # Target tokens by location [0, 1, 2, -1, -2]: none, the foes' actives,
        # this side's actives.
        actives = lambda hh, ff: [pick(hh, ff[..., c]) for c in (ACTIVE_SLOT0, ACTIVE_SLOT1)]
        targets = torch.stack([torch.zeros(B, d, device=h.device, dtype=h.dtype),
                               *actives(ht, ft), *actives(hm, fm)], 1)          # [B, 5, d]
        # Switch targets: the token at each party position.
        party = torch.stack([pick(hm, fm[..., PARTY + k]) for k in range(N_SWITCH)], 1)  # [B, 6, d]
        switch = self.switch(party)
        out = []
        for col in (ACTIVE_SLOT0, ACTIVE_SLOT1):
            w = fm[..., col]
            tok = pick(hm, w)
            ids = pick(ints[:, mi, 3:7].float(), w).round().long()            # [B, 4]
            moves = torch.cat([
                self.move_embedding(ids),                                                     # [B, 4, 12]
                pick(fm[..., PP:PP + 4], w).unsqueeze(-1),
                pick(fm[..., DISABLED:DISABLED + 4], w).unsqueeze(-1),
            ], -1)
            dmg = pick(fm[..., DAMAGE:DAMAGE + 20], w).view(B, 4, 5, 1)
            x = torch.cat([
                moves[:, :, None, None].expand(B, 4, 5, 2, moves.shape[-1]),
                dmg[:, :, :, None].expand(B, 4, 5, 2, 1),
                targets[:, None, :, None].expand(B, 4, 5, 2, d),
                self.target_kind[None, None, :, None].expand(B, 4, 5, 2, 5),
                self.mega[None, None, None, :, None].expand(B, 4, 5, 2, 1),
            ], -1)
            e = torch.cat([self.move(x).reshape(B, N_MOVE, d), switch,
                           self.pass_.expand(B, 1, d)], 1)                    # [B, 47, d]
            ctx = self.context(torch.cat([tok, g], -1)).unsqueeze(1)
            out.append(self.out(e + ctx))
        return out


class JointHead(nn.Module):
    """Logits [B, 47 * 47] for every pair of slot actions."""

    def __init__(self, d: int):
        super().__init__()
        self.u = nn.ModuleList([nn.Linear(d, 1), nn.Linear(d, 1)])
        self.p = nn.Linear(d, d, bias=False)
        self.q = nn.Linear(d, d, bias=False)
        self.scale = d ** -0.5

    def forward(self, e0, e1):
        u0 = self.u[0](e0).squeeze(-1)                                      # [B, 47]
        u1 = self.u[1](e1).squeeze(-1)
        pair = torch.bmm(self.p(e0), self.q(e1).transpose(1, 2)) * self.scale  # [B, 47, 47]
        return (u0.unsqueeze(2) + u1.unsqueeze(1) + pair).flatten(1)
