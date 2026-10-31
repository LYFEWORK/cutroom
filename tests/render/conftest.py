"""Fixtures for the render tests: synthetic media made once per session."""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "engine"))

import render_helpers as H  # noqa: E402

@pytest.fixture(scope="session")
def media(tmp_path_factory):
    return tmp_path_factory.mktemp("render-media")


@pytest.fixture(scope="session")
def small_cut(media):
    """a six second cut in the 9:16 shape, small enough that a plan that fails its checks costs nothing"""
    return H.make_cut(media / "small.mp4", 360, 640, 6.0)


@pytest.fixture(scope="session")
def cut_portrait(media):
    return H.make_cut(media / "cut-portrait.mp4", 1080, 1920, 6.0)


@pytest.fixture(scope="session")
def cut_landscape(media):
    return H.make_cut(media / "cut-landscape.mp4", 1920, 1080, 6.0)


@pytest.fixture(scope="session")
def cut_feed(media):
    return H.make_cut(media / "cut-feed.mp4", 1080, 1350, 6.0)


@pytest.fixture(scope="session")
def clip(media):
    return H.make_clip(media / "insert.mp4")


@pytest.fixture(scope="session")
def music(media):
    return H.make_music(media / "music.wav", 20.0)


@pytest.fixture(scope="session")
def click(media):
    return H.make_click(media / "click.wav")
