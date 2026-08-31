"""Fixtures shared by the parser tests.

Everything under test reads sysfs or procfs.  Rather than mocking `open`,
the parsers take their root paths as arguments (defaulted to the real ones),
so a test can build a small directory that looks like the kernel's and point
them at it.  That is what `fake_tree` is for.
"""

import os

import pytest


class Tree:
    """A throwaway directory that files can be dropped into by path."""

    def __init__(self, root):
        self.root = str(root)

    def write(self, relative, text):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def symlink(self, relative, target="."):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        return path

    def path(self, relative=""):
        return os.path.join(self.root, relative)


@pytest.fixture
def fake_tree(tmp_path):
    return Tree(tmp_path)
