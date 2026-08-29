from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinArtwork:
    shape_id: str
    label: str
    filename: str
    svg: str


def _svg(body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<g fill="#000000">{body}</g></svg>'
    )


BUILTIN_ARTWORK: tuple[BuiltinArtwork, ...] = (
    BuiltinArtwork("circle", "Circle", "basic-circle.svg", _svg(
        '<circle cx="50" cy="50" r="44"/>'
    )),
    BuiltinArtwork("square", "Square", "basic-square.svg", _svg(
        '<rect x="8" y="8" width="84" height="84"/>'
    )),
    BuiltinArtwork("triangle", "Triangle", "basic-triangle.svg", _svg(
        '<polygon points="50,5 96,92 4,92"/>'
    )),
    BuiltinArtwork("star", "Star", "basic-star.svg", _svg(
        '<polygon points="50,3 61,36 96,36 68,57 79,91 50,71 21,91 32,57 4,36 39,36"/>'
    )),
    BuiltinArtwork("heart", "Heart", "basic-heart.svg", _svg(
        '<path d="M50 92C43 84 9 62 9 34C9 15 32 7 50 27C68 7 91 15 91 34C91 62 57 84 50 92Z"/>'
    )),
    BuiltinArtwork("hexagon", "Hexagon", "basic-hexagon.svg", _svg(
        '<polygon points="25,7 75,7 97,50 75,93 25,93 3,50"/>'
    )),
    BuiltinArtwork("diamond", "Diamond", "basic-diamond.svg", _svg(
        '<polygon points="50,3 97,50 50,97 3,50"/>'
    )),
    BuiltinArtwork("arrow", "Arrow", "basic-arrow.svg", _svg(
        '<path d="M5 38H58V14L96 50L58 86V62H5Z"/>'
    )),
)

_BY_ID = {shape.shape_id: shape for shape in BUILTIN_ARTWORK}


def builtin_artwork(shape_id: str) -> BuiltinArtwork:
    try:
        return _BY_ID[shape_id]
    except (KeyError, TypeError) as exc:
        raise ValueError("Choose a valid built-in artwork shape.") from exc

