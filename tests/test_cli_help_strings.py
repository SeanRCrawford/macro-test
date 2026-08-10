"""Every argparse help string must survive argparse's own % formatting.

argparse renders help through the % operator, so a literal percent sign in a
help string is an incomplete format spec. Python 3.12 and earlier only raise
when --help is actually printed; Python 3.14 added an eager check in
add_argument, which turns the same latent bug into an immediate crash on
startup -- no --help needed. That is how it was found: a help string reading
"recalls ~100%" killed tools/search_teams.py at launch on 3.14 while passing
silently on 3.11.

This walks the source rather than importing the tools, so it is fast, has no
side effects, and catches the fault on any interpreter version regardless of
which one is running the tests.
"""
import ast
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SEARCH_DIRS = ["src", "tools"]

# The substitutions argparse itself makes available. A help string using any
# other key is equally broken, and the KeyError below reports it.
PARAMS = {"default": "d", "prog": "p", "type": "t", "choices": "c",
          "dest": "x", "metavar": "m", "const": "k", "nargs": "n"}


def _python_files():
    for directory in SEARCH_DIRS:
        base = os.path.join(ROOT, directory)
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            if entry.endswith(".py"):
                yield os.path.join(base, entry)


def _help_strings(path):
    """(lineno, text) for every literal help= on an add_argument call."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "help":
                continue
            try:
                text = ast.literal_eval(keyword.value)
            except (ValueError, TypeError):
                continue          # computed at runtime, nothing to check here
            if isinstance(text, str):
                yield node.lineno, text


class TestCliHelpStrings(unittest.TestCase):

    def test_every_help_string_formats(self):
        failures = []
        for path in _python_files():
            for lineno, text in _help_strings(path):
                try:
                    text % PARAMS
                except Exception as exc:
                    failures.append(
                        f"{os.path.relpath(path, ROOT)}:{lineno} "
                        f"{type(exc).__name__}: {exc}\n"
                        f"    ...{text[-70:]!r}\n"
                        f"    (a literal percent must be written %%)")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_the_checker_actually_catches_a_bare_percent(self):
        """A test that cannot fail is not a test."""
        with self.assertRaises(ValueError):
            "recalls ~100%." % PARAMS
        self.assertEqual("recalls ~100%%." % PARAMS, "recalls ~100%.")

    def test_some_help_strings_were_found(self):
        """Guards against the AST walk silently matching nothing."""
        total = sum(1 for path in _python_files() for _ in _help_strings(path))
        self.assertGreater(total, 20, "no help strings found -- checker is broken")


if __name__ == "__main__":
    unittest.main()
