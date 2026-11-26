# *******************************************************************************
# Copyright (c) 2026 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License Version 2.0 which is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
# *******************************************************************************
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

"""Render the PlantUML diagrams of all SCORE repositories with several layout
engines and compare the results.

Two runs:

* ``ortho``: diagrams that ask for ``linetype ortho``, rendered with Graphviz
  and with Smetana's orthogonal routing.
* ``other``: all other diagrams, rendered with Graphviz and with Smetana.

See README.md for the meaning of the reported numbers.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime
import html
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent.parent
PLANTUML_REPOSITORY = "https://github.com/plantuml/plantuml"
PLANTUML_BRANCH = "smetana-orthogonal-routing"
DIAGRAM_SUFFIXES = {".puml", ".plantuml", ".iuml", ".pu"}
SKIPPED_DIRECTORIES = {".git", "node_modules", "_build", ".venv"}

BASELINE = "graphviz"
VARIANTS: dict[str, list[str]] = {
    "graphviz": [],
    "smetana": ["-Playout=smetana"],
    "smetana-ortho": ["-Playout=smetana", "-Psmetana_ortho=true"],
}
RUNS: dict[str, list[str]] = {
    "ortho": ["graphviz", "smetana-ortho"],
    "other": ["graphviz", "smetana"],
}
RUN_TITLES = {"ortho": "Orthogonal lines", "other": "Other diagrams"}
RUN_HINTS = {
    "ortho": "Diagrams that set linetype ortho",
    "other": "Diagrams that do not set linetype ortho",
}

ORTHO = re.compile(r"linetype\s+ortho", re.IGNORECASE)
INCLUDE = re.compile(r"^\s*!include(?:_once|_many|sub)?\s+(\S+)", re.MULTILINE)
# A diagram with something between its start and end. Comments do not count.
DIAGRAM = re.compile(
    r"^\s*@start[^\n]*\n(?:\s*(?:'[^\n]*)?\n)*\s*(?!@end|'|\s)\S", re.MULTILINE
)


# --------------------------------------------------------------------------
# Preparation: repository cache and PlantUML jar
# --------------------------------------------------------------------------


def run(
    command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=cwd, env=env)


def sync_cache(org: str) -> None:
    run(
        [
            "uvx",
            "--from",
            str(TOOLS_ROOT / "repo_cache"),
            "score-repo-cache",
            "sync",
            "--org",
            org,
        ]
    )


def build_jar(out: Path, jdk: Path | None) -> tuple[Path, str]:
    """Clone or update the PlantUML branch and build its jar."""
    source = out / "plantuml-src"
    if (source / ".git").is_dir():
        run(
            [
                "git",
                "-C",
                str(source),
                "fetch",
                "--depth",
                "1",
                "origin",
                PLANTUML_BRANCH,
            ]
        )
        run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    else:
        run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                PLANTUML_BRANCH,
                PLANTUML_REPOSITORY,
                str(source),
            ]
        )
    command = ["./gradlew", "jar"]
    environment = dict(os.environ)
    if jdk is not None:
        environment["JAVA_HOME"] = str(jdk)
        command.append(f"-Porg.gradle.java.installations.paths={jdk}")
    run(command, cwd=source, env=environment)
    return find_jar(source), jar_commit(source)


def find_jar(source: Path) -> Path:
    jars = [
        jar
        for jar in (source / "build" / "libs").glob("plantuml-*.jar")
        if not jar.stem.endswith(("-sources", "-javadoc"))
    ]
    if not jars:
        sys.exit(
            f"No PlantUML jar in {source / 'build' / 'libs'}. Run without --skip-build."
        )
    return max(jars, key=lambda jar: jar.stat().st_mtime)


def jar_commit(source: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), "log", "-1", "--format=%h %s"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


# --------------------------------------------------------------------------
# Collecting diagrams
# --------------------------------------------------------------------------


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def collect(cache: Path, repos: list[str]) -> list[Path]:
    """All files that contain a diagram of their own, not only fragments to include or an empty frame."""
    found = []
    for repo in sorted(path for path in cache.iterdir() if path.is_dir()):
        if repos and repo.name not in repos:
            continue
        for directory, subdirectories, files in os.walk(repo):
            subdirectories[:] = [
                name
                for name in subdirectories
                if name not in SKIPPED_DIRECTORIES and not name.startswith("bazel-")
            ]
            for name in files:
                path = Path(directory) / name
                if path.suffix in DIAGRAM_SUFFIXES and DIAGRAM.search(read(path)):
                    found.append(path)
    return sorted(found)


def asks_for_ortho(path: Path, seen: set[Path] | None = None) -> bool:
    """Whether the file or one of its local includes sets ``linetype ortho``."""
    seen = seen if seen is not None else set()
    if path in seen or not path.is_file():
        return False
    seen.add(path)
    text = read(path)
    if ORTHO.search(text):
        return True
    for target in INCLUDE.findall(text):
        if target.startswith(("<", "http://", "https://")):
            continue
        included = (path.parent / target.split("!")[0]).resolve()
        if asks_for_ortho(included, seen):
            return True
    return False


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


@dataclass
class Render:
    status: str  # ok, error, timeout
    seconds: float
    returncode: int | None


def render(
    java: str, jar: Path, source: Path, target: Path, flags: list[str], timeout: float
) -> Render:
    """Render the first diagram of a file. Local includes resolve next to the file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        java,
        "-Djava.awt.headless=true",
        "-jar",
        str(jar),
        "-tsvg",
        *flags,
        "-pipe",
    ]
    started = time.monotonic()
    with (
        source.open("rb") as stdin,
        target.open("wb") as stdout,
        target.with_suffix(".err").open("wb") as stderr,
    ):
        # A session of its own, so that a timeout also ends a running dot.
        process = subprocess.Popen(
            command,
            cwd=source.parent,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return Render("timeout", time.monotonic() - started, None)
    seconds = time.monotonic() - started
    failed = returncode != 0 or target.stat().st_size == 0
    return Render("error" if failed else "ok", seconds, returncode)


# --------------------------------------------------------------------------
# Measuring an SVG
# --------------------------------------------------------------------------

Point = tuple[float, float]
Box = tuple[float, float, float, float]

LINK = re.compile(r'<g class="link".*?</g>', re.DOTALL)
PATH = re.compile(r'<path d="([^"]*)"')
PATH_TOKEN = re.compile(r"[MLCQZ]|-?\d+(?:\.\d+)?(?:e-?\d+)?", re.IGNORECASE)
TEXT = re.compile(r"<text ([^>]*)>([^<]*)<")
ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')
METADATA = re.compile(r'<\?plantuml-src.*?\?>|data-source-line="\d+"', re.DOTALL)
ARGUMENTS = {"M": 2, "L": 2, "C": 6, "Q": 4}
TOLERANCE = 0.01
# A connector may touch another one where both leave the same node.
ENDPOINT_CLEARANCE = 3.0


@dataclass
class Measures:
    diagram_type: str = ""
    width: float = 0
    height: float = 0
    edges: int = 0
    orthogonal_edges: int = 0
    label_overlaps: int = 0
    crossings: int = 0
    edge_length: float = 0
    bends: int = 0
    crashed: bool = False


@dataclass
class Edge:
    # Each piece is a straight line or a curve, given by all its points.
    pieces: list[list[Point]] = field(default_factory=list)

    def is_orthogonal(self) -> bool:
        return bool(self.pieces) and all(
            all(abs(point[0] - piece[0][0]) < TOLERANCE for point in piece)
            or all(abs(point[1] - piece[0][1]) < TOLERANCE for point in piece)
            for piece in self.pieces
        )

    def bends(self) -> int:
        """How often an orthogonal edge turns."""
        runs = [
            abs(piece[0][0] - piece[-1][0]) < TOLERANCE
            for piece in self.pieces
            if math.dist(piece[0], piece[-1]) >= TOLERANCE
        ]
        return sum(vertical != following for vertical, following in zip(runs, runs[1:]))

    def polyline(self) -> list[Point]:
        points = [self.pieces[0][0]]
        for piece in self.pieces:
            if len(piece) == 2:
                points.append(piece[1])
            else:
                points.extend(bezier(piece, step / 8) for step in range(1, 9))
        return points


def bezier(control: list[Point], t: float) -> Point:
    points = control
    while len(points) > 1:
        points = [
            (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            for a, b in zip(points, points[1:])
        ]
    return points[0]


def parse_path(data: str) -> Edge | None:
    """The first subpath of an SVG path made of absolute M, L, C and Q commands."""
    edge = Edge()
    tokens = PATH_TOKEN.findall(data)
    position: Point | None = None
    command = ""
    index = 0
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
            if command not in ARGUMENTS or (command == "M" and position is not None):
                break
            continue
        count = ARGUMENTS.get(command, 0)
        numbers = tokens[index : index + count]
        if (
            count == 0
            or len(numbers) < count
            or any(token.isalpha() for token in numbers)
        ):
            break
        index += count
        values = [float(number) for number in numbers]
        points = list(zip(values[0::2], values[1::2]))
        if command == "M":
            position = points[0]
            command = "L"
            continue
        if position is None:
            break
        edge.pieces.append([position, *points])
        position = points[-1]
    return edge if edge.pieces else None


def text_boxes(group: str) -> list[Box]:
    """Approximate bounds of the texts of a link, from their position and length."""
    boxes = []
    for attributes, content in TEXT.findall(group):
        values = dict(ATTRIBUTE.findall(attributes))
        if not content.strip() or "x" not in values or "y" not in values:
            continue
        try:
            x = float(values["x"])
            y = float(values["y"])
            size = float(values.get("font-size", "13"))
            width = float(values.get("textLength", size * 0.5 * len(content)))
        except ValueError:
            continue
        boxes.append((x, y - size * 0.8, x + width, y + size * 0.2))
    return boxes


def overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def bounds(points: list[Point]) -> Box:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def intersection(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Where the segments a-b and c-d cross. Parallel segments do not cross."""
    denominator = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    if abs(denominator) < 1e-9:
        return None
    t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / denominator
    u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / denominator
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return None


def near(point: Point, others: list[Point]) -> bool:
    return any(
        abs(point[0] - other[0]) < ENDPOINT_CLEARANCE
        and abs(point[1] - other[1]) < ENDPOINT_CLEARANCE
        for other in others
    )


def count_crossings(lines: list[list[Point]]) -> int:
    """Pairs of connectors that cross away from their ends."""
    boxes = [bounds(line) for line in lines]
    crossings = 0
    for first in range(len(lines)):
        for second in range(first + 1, len(lines)):
            if not overlap(boxes[first], boxes[second]):
                continue
            a, b = lines[first], lines[second]
            ends = [a[0], a[-1], b[0], b[-1]]
            if any(
                (point := intersection(a[i], a[i + 1], b[j], b[j + 1])) is not None
                and not near(point, ends)
                for i in range(len(a) - 1)
                for j in range(len(b) - 1)
            ):
                crossings += 1
    return crossings


def number(text: str, pattern: str) -> float:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else 0


def measure(svg: str) -> Measures:
    head = svg[:2000]
    measures = Measures(
        diagram_type=(re.search(r'data-diagram-type="([^"]*)"', head) or ["", ""])[1],
        width=number(head, r'\bwidth="([\d.]+)px"'),
        height=number(head, r'\bheight="([\d.]+)px"'),
        crashed="An error has occured" in svg or "An error has occurred" in svg,
    )
    lines = []
    labels: list[list[Box]] = []
    for group in LINK.findall(svg):
        labels.append(text_boxes(group))
        # The longest path is the connector. Shorter ones are its decorations.
        edges = [
            edge for edge in map(parse_path, PATH.findall(group)) if edge is not None
        ]
        if not edges:
            continue
        edge = max(edges, key=lambda candidate: len(candidate.pieces))
        measures.edges += 1
        line = edge.polyline()
        measures.edge_length += sum(math.dist(a, b) for a, b in zip(line, line[1:]))
        if edge.is_orthogonal():
            measures.orthogonal_edges += 1
            measures.bends += edge.bends()
        lines.append(line)
    measures.crossings = count_crossings(lines)
    measures.label_overlaps = sum(
        overlap(a, b)
        for first in range(len(labels))
        for second in range(first + 1, len(labels))
        for a in labels[first]
        for b in labels[second]
    )
    return measures


# --------------------------------------------------------------------------
# Comparing two pictures of the same diagram
# --------------------------------------------------------------------------

ELEMENT = re.compile(r'<g class="(?:entity|cluster)"([^>]*)>(.*?)</g>', re.DOTALL)
LINK_ENDS = re.compile(r'<g class="link"([^>]*)>(.*?)</g>', re.DOTALL)
SHAPE = re.compile(r"<(rect|ellipse|line|polygon|text) ([^>]*)>")


@dataclass
class Layout:
    # Where each element is, by its name in the diagram.
    boxes: dict[str, Box]
    # Each connector with the elements it joins, in the order of the file.
    edges: list[tuple[str, str, list[Point]]]


@dataclass
class Alike:
    """How much two pictures of a diagram have in common, from 0 to 1. None without anything to compare."""

    arrangement: float | None = None
    edge_sides: float | None = None

    def both(self) -> float | None:
        known = [
            value for value in (self.arrangement, self.edge_sides) if value is not None
        ]
        return sum(known) / len(known) if known else None


def shape_points(kind: str, values: dict[str, str]) -> list[Point]:
    def at(*names: str) -> list[float]:
        return [float(values[name]) for name in names]

    try:
        if kind == "rect":
            x, y, width, height = at("x", "y", "width", "height")
            return [(x, y), (x + width, y + height)]
        if kind == "ellipse":
            x, y, rx, ry = at("cx", "cy", "rx", "ry")
            return [(x - rx, y - ry), (x + rx, y + ry)]
        if kind == "line":
            x1, y1, x2, y2 = at("x1", "y1", "x2", "y2")
            return [(x1, y1), (x2, y2)]
        if kind == "polygon":
            numbers = [
                float(number)
                for number in re.findall(r"-?\d+(?:\.\d+)?", values["points"])
            ]
            return list(zip(numbers[0::2], numbers[1::2]))
        return [(at("x")[0], at("y")[0])]
    except (KeyError, ValueError):
        return []


def layout(svg: str) -> Layout:
    names: dict[str, str] = {}
    boxes: dict[str, Box] = {}
    for attributes, body in ELEMENT.findall(svg):
        values = dict(ATTRIBUTE.findall(attributes))
        name = values.get("data-qualified-name") or values.get("id", "")
        names[values.get("id", name)] = name
        points = [
            point
            for kind, shape in SHAPE.findall(body)
            for point in shape_points(kind, dict(ATTRIBUTE.findall(shape)))
        ]
        if not points:
            points = [
                point
                for edge in map(parse_path, PATH.findall(body))
                if edge is not None
                for point in edge.polyline()
            ]
        if points:
            boxes[name] = bounds(points)
    edges = []
    for attributes, body in LINK_ENDS.findall(svg):
        values = dict(ATTRIBUTE.findall(attributes))
        paths = [
            edge for edge in map(parse_path, PATH.findall(body)) if edge is not None
        ]
        if paths and "data-entity-1" in values and "data-entity-2" in values:
            first = names.get(values["data-entity-1"], values["data-entity-1"])
            second = names.get(values["data-entity-2"], values["data-entity-2"])
            longest = max(paths, key=lambda candidate: len(candidate.pieces))
            edges.append((first, second, longest.polyline()))
    return Layout(boxes, edges)


def relation(a: Box, b: Box, axis: int) -> int:
    """Whether a lies before b, after it, or beside it along an axis."""
    if a[axis + 2] <= b[axis]:
        return -1
    if b[axis + 2] <= a[axis]:
        return 1
    return 0


def distance(point: Point, box: Box) -> float:
    dx = max(box[0] - point[0], 0, point[0] - box[2])
    dy = max(box[1] - point[1], 0, point[1] - box[3])
    return dx + dy


def side(point: Point, box: Box) -> int:
    """The side of an element a connector end is attached to: left, top, right or bottom."""
    gaps = [
        abs(point[0] - box[0]),
        abs(point[1] - box[1]),
        abs(point[0] - box[2]),
        abs(point[1] - box[3]),
    ]
    return gaps.index(min(gaps))


def sides(
    edge: tuple[str, str, list[Point]], boxes: dict[str, Box]
) -> tuple[int, int] | None:
    """The sides at which a connector leaves its first element and reaches its second."""
    first, second, line = edge
    if first == second or first not in boxes or second not in boxes:
        return None
    a, b = boxes[first], boxes[second]
    start, end = line[0], line[-1]
    # A connector may be drawn from its second element to its first.
    if distance(start, a) + distance(end, b) > distance(start, b) + distance(end, a):
        start, end = end, start
    return side(start, a), side(end, b)


def compare(mine: str, theirs: str) -> Alike:
    """Compares the arrangement of the elements and the sides the connectors attach to."""
    a, b = layout(mine), layout(theirs)
    alike = Alike()

    shared = sorted(set(a.boxes) & set(b.boxes))
    kept = pairs = 0
    for index, first in enumerate(shared):
        for second in shared[index + 1 :]:
            for axis in (0, 1):
                pairs += 1
                kept += relation(a.boxes[first], a.boxes[second], axis) == relation(
                    b.boxes[first], b.boxes[second], axis
                )
    if pairs:
        alike.arrangement = kept / pairs

    # Connectors between the same two elements are matched in the order of the file.
    waiting: dict[tuple[str, str], list[tuple[int, int] | None]] = {}
    for edge in b.edges:
        waiting.setdefault(edge[:2], []).append(sides(edge, b.boxes))
    kept = ends = 0
    for edge in a.edges:
        others = waiting.get(edge[:2])
        if not others:
            continue
        other = others.pop(0)
        own = sides(edge, a.boxes)
        if own is None or other is None:
            continue
        ends += 2
        kept += (own[0] == other[0]) + (own[1] == other[1])
    if ends:
        alike.edge_sides = kept / ends
    return alike


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


@dataclass
class Result:
    repo: str
    path: str
    variant: str
    status: str
    seconds: float
    svg: str
    diagram_type: str = ""
    width: float = 0
    height: float = 0
    edges: int = 0
    orthogonal_edges: int = 0
    label_overlaps: int = 0
    crossings: int = 0
    edge_length: float = 0
    bends: int = 0

    @property
    def area(self) -> float:
        return self.width * self.height


def process(job: tuple[Path, Path, str, argparse.Namespace, Path, str]) -> Result:
    source, cache, variant, options, run_directory, java = job
    relative = source.relative_to(cache)
    target = run_directory / variant / relative.with_name(relative.name + ".svg")
    record = target.with_suffix(".json")
    if options.reuse and record.is_file() and target.is_file():
        rendered = Render(**json.loads(record.read_text()))
    else:
        rendered = render(
            java, options.jar, source, target, VARIANTS[variant], options.timeout
        )
        record.write_text(json.dumps(asdict(rendered)))
    result = Result(
        repo=relative.parts[0],
        path=str(Path(*relative.parts[1:])),
        variant=variant,
        status=rendered.status,
        seconds=round(rendered.seconds, 2),
        svg=str(target.relative_to(run_directory)),
    )
    if (
        rendered.status == "timeout"
        or not target.is_file()
        or target.stat().st_size == 0
    ):
        return result
    measures = measure(read(target))
    if measures.crashed:
        result.status = "crash"
    for name in (
        "diagram_type",
        "width",
        "height",
        "edges",
        "orthogonal_edges",
        "label_overlaps",
        "crossings",
        "edge_length",
        "bends",
    ):
        setattr(result, name, getattr(measures, name))
    return result


@dataclass
class Report:
    name: str
    variants: list[str]
    # The compared diagrams, by repository and path, the most conspicuous first.
    ranked: list[tuple[tuple[str, str], dict[str, Result]]]
    # What both pictures of a diagram have in common, where both exist.
    alike: dict[tuple[str, str], Alike]
    run_directory: Path


def is_trivial(result: Result) -> bool:
    """With one edge at most and no flaw, a layout has nothing to get wrong."""
    return (
        result.status == "ok"
        and result.edges <= 1
        and result.crossings == 0
        and result.label_overlaps == 0
    )


def same_picture(run_directory: Path, results: list[Result]) -> bool:
    """Whether every variant painted the same. Such a diagram is not laid out by either engine."""
    if any(result.status != "ok" for result in results):
        return False
    pictures = {
        METADATA.sub("", read(run_directory / result.svg)) for result in results
    }
    return len(pictures) == 1


def execute(
    name: str, sources: list[Path], options: argparse.Namespace, java: str
) -> Report:
    variants = RUNS[name]
    run_directory = options.out / name
    run_directory.mkdir(parents=True, exist_ok=True)
    jobs = [
        (source, options.cache, variant, options, run_directory, java)
        for source in sources
        for variant in variants
    ]
    print(
        f"[{name}] {len(sources)} diagrams, {len(jobs)} renders, {options.jobs} at a time",
        flush=True,
    )
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=options.jobs) as pool:
        for done, result in enumerate(pool.map(process, jobs), start=1):
            results.append(result)
            if done % 100 == 0 or done == len(jobs):
                print(f"[{name}] {done}/{len(jobs)}", flush=True)

    diagrams: dict[tuple[str, str], dict[str, Result]] = {}
    for result in results:
        diagrams.setdefault((result.repo, result.path), {})[result.variant] = result
    compared = {}
    unaffected = broken = trivial = 0
    for key, by_variant in diagrams.items():
        if all(result.status == "error" for result in by_variant.values()):
            broken += 1
        elif same_picture(run_directory, list(by_variant.values())):
            unaffected += 1
        elif all(is_trivial(result) for result in by_variant.values()):
            trivial += 1
        else:
            compared[key] = by_variant

    write_csv(run_directory / "results.csv", results)
    counts = {
        "diagrams": len(diagrams),
        "unaffected": unaffected,
        "broken": broken,
        "trivial": trivial,
    }
    ranked = sorted(
        compared.items(), key=lambda item: (-attention(item[1], variants), item[0])
    )
    (run_directory / "summary.md").write_text(
        summary(name, variants, ranked, counts, options)
    )
    print(f"[{name}] {len(compared)} diagrams compared", flush=True)
    alike = {
        key: compare(
            *(
                read(run_directory / by_variant[variant].svg)
                for variant in reversed(variants)
            )
        )
        for key, by_variant in compared.items()
        if all(result.status == "ok" for result in by_variant.values())
    }
    return Report(name, variants, ranked, alike, run_directory)


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def write_csv(path: Path, results: list[Result]) -> None:
    with path.open("w", newline="") as file:
        writer = (
            csv.DictWriter(file, fieldnames=list(asdict(results[0])))
            if results
            else None
        )
        if writer is None:
            return
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))


def attention(by_variant: dict[str, Result], variants: list[str]) -> float:
    """How much a diagram deserves a look: failures first, then what got worse than the baseline."""
    baseline = by_variant[BASELINE]
    score = 0.0
    for variant in variants:
        result = by_variant[variant]
        if variant == BASELINE:
            continue
        if result.status != "ok" and baseline.status == "ok":
            score += 1000
        elif result.status == "ok" and baseline.status == "ok":
            score += 10 * max(0, result.crossings - baseline.crossings)
            score += 10 * max(0, result.label_overlaps - baseline.label_overlaps)
            if baseline.area > 0:
                score += max(0.0, result.area / baseline.area - 1)
    return score


def table(header: list[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def summary(
    name: str,
    variants: list[str],
    ranked: list[tuple[tuple[str, str], dict[str, Result]]],
    counts: dict[str, int],
    options: argparse.Namespace,
) -> str:
    compared = [by_variant for _, by_variant in ranked]
    text = [f"# PlantUML layout comparison: {name}\n"]
    text.append(f"- PlantUML: `{options.commit}`")
    text.append(f"- Diagrams found: {counts['diagrams']}")
    text.append(f"- Compared: {len(compared)}")
    text.append(
        f"- Left out, every variant paints the same picture: {counts['unaffected']}"
    )
    text.append(f"- Left out, no variant can render it: {counts['broken']}")
    text.append(f"- Left out, nothing to get wrong: {counts['trivial']}\n")

    text.append("## Totals\n")
    rows = []
    for variant in variants:
        results = [by_variant[variant] for by_variant in compared]
        fine = [result for result in results if result.status == "ok"]
        ratios = [
            by_variant[variant].area / by_variant[BASELINE].area
            for by_variant in compared
            if by_variant[variant].status == "ok"
            and by_variant[BASELINE].status == "ok"
            and by_variant[BASELINE].area > 0
        ]
        edges = sum(result.edges for result in fine)
        rows.append(
            [
                variant,
                len(fine),
                sum(result.status in ("error", "crash") for result in results),
                sum(result.status == "timeout" for result in results),
                f"{sum(result.seconds for result in results):.0f} s",
                edges,
                sum(result.orthogonal_edges for result in fine),
                sum(result.crossings for result in fine),
                sum(result.label_overlaps for result in fine),
                f"{statistics.median(ratios):.2f}" if ratios else "-",
            ]
        )
    text.append(
        table(
            [
                "Variant",
                "ok",
                "failed",
                "timeout",
                "time",
                "edges",
                "orthogonal",
                "crossings",
                "label overlaps",
                "median area / graphviz",
            ],
            rows,
        )
    )

    text.append("## Diagram by diagram against graphviz\n")
    text.append(
        "Sums are dominated by a few large diagrams. This counts diagrams instead.\n"
    )
    rows = []
    for variant in variants:
        if variant == BASELINE:
            continue
        pairs = [
            (by_variant[variant], by_variant[BASELINE])
            for by_variant in compared
            if by_variant[variant].status == "ok"
            and by_variant[BASELINE].status == "ok"
        ]
        for name in ("crossings", "label_overlaps"):
            values = [
                (int(getattr(result, name)), int(getattr(baseline, name)))
                for result, baseline in pairs
            ]
            rows.append(
                [
                    variant,
                    name.replace("_", " "),
                    sum(mine < theirs for mine, theirs in values),
                    sum(mine == theirs for mine, theirs in values),
                    sum(mine > theirs for mine, theirs in values),
                ]
            )
    text.append(table(["Variant", "Number", "fewer", "same", "more"], rows))

    text.append("## Per repository\n")
    repos = sorted({key[0] for key, _ in ranked})
    rows = []
    for repo in repos:
        of_repo = [by_variant for key, by_variant in ranked if key[0] == repo]
        row: list[object] = [repo, len(of_repo)]
        for variant in variants:
            fine = [
                by_variant[variant]
                for by_variant in of_repo
                if by_variant[variant].status == "ok"
            ]
            row.append(
                f"{len(of_repo) - len(fine)} / {sum(result.crossings for result in fine)}"
                f" / {sum(result.label_overlaps for result in fine)}"
            )
        rows.append(row)
    text.append("Per variant: not rendered / crossings / label overlaps.\n")
    text.append(table(["Repository", "diagrams", *variants], rows))

    failures = [
        [f"{key[0]}/{key[1]}", variant, by_variant[variant].status]
        for key, by_variant in ranked
        for variant in variants
        if variant != BASELINE
        and by_variant[variant].status != "ok"
        and by_variant[BASELINE].status == "ok"
    ]
    text.append(f"## Rendered by graphviz only ({len(failures)})\n")
    if failures:
        text.append(table(["Diagram", "Variant", "Status"], failures[:50]))

    text.append("## Diagrams to look at first\n")
    rows = []
    for key, by_variant in ranked[:30]:
        if attention(by_variant, variants) <= 0:
            break
        row = [f"{key[0]}/{key[1]}"]
        for variant in variants:
            result = by_variant[variant]
            row.append(
                f"{result.crossings} / {result.label_overlaps} / {result.width:.0f}×{result.height:.0f}"
                if result.status == "ok"
                else result.status
            )
        rows.append(row)
    text.append("Per variant: crossings / label overlaps / size.\n")
    text.append(table(["Diagram", *variants], rows))
    text.append("All diagrams side by side: `../index.html`.\n")
    return "\n".join(text)


VARIANT_TITLES = {
    "graphviz": "Graphviz",
    "smetana": "Smetana",
    "smetana-ortho": "Smetana, orthogonal routing",
}
VARIANT_HINTS = {
    "graphviz": "PlantUML's default, an external tool",
    "smetana": "built into PlantUML",
    "smetana-ortho": "built into PlantUML, with its new orthogonal routing",
}
HINTS = {
    "rendered": "Diagrams the engine could render",
    "edges": "Connectors between the elements",
    "orthogonal": "Edges made of horizontal and vertical pieces only",
    "crossings": "Pairs of edges that cross away from their ends. Overcounted where edges run in a bundle.",
    "label overlaps": "Pairs of labels of different edges that overlap",
    "diagrams with fewer crossings": "Diagrams in which this engine has fewer crossings than the other one",
    "edge length": "Length of all edges together, in pixels",
    "bends": "How often the orthogonal edges turn, all together",
    "size": "Width and height of the picture in pixels",
    "same arrangement": "Pairs of elements that lie the same way to each other in both pictures:"
    " left or right, above or below",
    "same edge sides": "Ends of edges that are attached to the same side of their element in both pictures",
    "seconds": "Rendering time of a diagram, including the start of Java",
}
STYLE = """
:root { --line: #d9dee5; --muted: #5d6877; --soft: #f5f7fa; --good: #1a7f4b; --bad: #b3261e; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 system-ui, sans-serif; color: #1c2430; background: #fff; }
main { max-width: 1600px; margin: 0 auto; padding: 28px 24px 80px; }
h1 { margin: 0; font-size: 26px; }
p { margin: 6px 0; }
code { background: var(--soft); padding: 1px 5px; border-radius: 4px; font-size: 13px; }
a { color: #1f5fbf; }
.muted { color: var(--muted); }
.good { color: var(--good); }
.bad { color: var(--bad); }
.tabs { display: flex; gap: 4px; margin-top: 20px; border-bottom: 2px solid var(--line); }
.tabs button { font: inherit; font-weight: 600; padding: 8px 18px; border: 0; background: none;
  color: var(--muted); border-bottom: 3px solid transparent; margin-bottom: -2px; cursor: pointer; }
.tabs button.active { color: inherit; border-bottom-color: #1f5fbf; }
.tabs small { font-weight: normal; color: var(--muted); }
.filters { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 12px 20px;
  align-items: center; padding: 12px 0; background: #fff;
  border-bottom: 1px solid var(--line); }
.filters input[type=search], .filters select { font: inherit; padding: 5px 8px;
  border: 1px solid var(--line); border-radius: 6px; }
.card { margin-top: 24px; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.card header { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: baseline;
  padding: 10px 16px; background: var(--soft); border-bottom: 1px solid var(--line); }
.card header .name { font-weight: 600; overflow-wrap: anywhere; }
.repository { padding: 1px 9px; border-radius: 999px; border: 1px solid var(--line);
  background: #fff; font-size: 13px; }
.compare { display: grid; grid-template-columns: minmax(0, 1fr) 300px minmax(0, 1fr);
  align-items: start; padding: 14px 16px; gap: 16px; }
.compare h2 { margin: 0 0 10px; font-size: 15px; text-align: center; }
.compare h2 small { display: block; font-weight: normal; color: var(--muted); }
.compare img { display: block; max-width: 100%; max-height: 560px; margin: 0 auto; cursor: zoom-in; }
.failed { padding: 40px 0; text-align: center; color: var(--bad); }
.numbers { display: grid; grid-template-columns: 1fr auto 1fr; gap: 4px 10px; align-self: center;
  font-variant-numeric: tabular-nums; }
.numbers span { white-space: nowrap; }
.numbers .left { text-align: right; }
.numbers .what { text-align: center; color: var(--muted); font-size: 13px; cursor: help;
  align-self: center; }
.numbers b { margin-left: 6px; }
.numbers .wide { grid-column: 1 / -1; text-align: center; color: var(--muted); font-size: 13px;
  cursor: help; }
.numbers .wide b { margin: 0; color: #1c2430; font-size: 15px; }
.numbers .wide:first-of-type { margin-top: 8px; }
.summary .numbers { font-size: 17px; }
#zoom { position: fixed; inset: 0; z-index: 5; display: none; overflow: auto; padding: 24px;
  background: rgba(255, 255, 255, 0.97); cursor: zoom-out; }
#zoom.open { display: block; }
#zoom img { display: block; margin: 0 auto; }
"""
SCRIPT = """
const tabs = [...document.querySelectorAll('.tab')];
const buttons = [...document.querySelectorAll('.tabs button')];
const search = document.getElementById('search');
const repository = document.getElementById('repository');
const changed = document.getElementById('changed');
const sort = document.getElementById('sort');
const shown = document.getElementById('shown');
const orders = {
  worse: (a, b) => b.dataset.added - a.dataset.added || a.dataset.rank - b.dataset.rank,
  better: (a, b) => a.dataset.added - b.dataset.added || a.dataset.rank - b.dataset.rank,
  different: (a, b) => a.dataset.alike - b.dataset.alike || a.dataset.rank - b.dataset.rank,
  complex: (a, b) => b.dataset.edges - a.dataset.edges || a.dataset.rank - b.dataset.rank,
};
let active = tabs.find(tab => '#' + tab.id === location.hash) || tabs[0];
function update() {
  for (const tab of tabs) tab.hidden = tab !== active;
  for (const button of buttons) button.classList.toggle('active', button.dataset.tab === active.id);
  const cards = [...active.querySelectorAll('.diagram')];
  const text = search.value.toLowerCase();
  let count = 0;
  for (const card of cards) {
    const visible = card.dataset.name.includes(text)
      && (repository.value === '' || card.dataset.repository === repository.value)
      && (!changed.checked || card.dataset.changed === 'yes');
    card.hidden = !visible;
    count += visible;
  }
  active.querySelector('.cards').append(...cards.sort(orders[sort.value]));
  shown.textContent = count + ' of ' + cards.length + ' diagrams';
}
for (const control of [search, repository, changed, sort]) control.addEventListener('input', update);
for (const button of buttons) button.addEventListener('click', () => {
  active = tabs.find(tab => tab.id === button.dataset.tab);
  history.replaceState(null, '', '#' + active.id);
  update();
});
update();
const zoom = document.getElementById('zoom');
document.addEventListener('click', event => {
  if (zoom.classList.contains('open')) { zoom.classList.remove('open'); return; }
  if (event.target.matches('.compare img')) {
    zoom.firstElementChild.src = event.target.src;
    zoom.classList.add('open');
  }
});
"""


def delta(mine: int, theirs: int) -> str:
    """How a number differs from the Graphviz one, where more is worse."""
    if mine == theirs:
        return ""
    return f'<b class="{"bad" if mine > theirs else "good"}">{mine - theirs:+d}</b>'.replace(
        "-", "−"
    )


def growth(mine: float, theirs: float) -> str:
    """How a length differs from the Graphviz one in percent, where longer is worse."""
    change = mine / theirs - 1 if theirs else 0
    if abs(change) < 0.005:
        return ""
    return f'<b class="{"bad" if change > 0 else "good"}">{change:+.0%}</b>'.replace(
        "-", "−"
    )


def row(left: object, what: str, right: object, change: str = "") -> str:
    return (
        f'<span class="left">{left}</span><span class="what" title="{html.escape(HINTS[what])}">{what}</span>'
        f"<span>{right}{change}</span>"
    )


def wide(share: float, what: str) -> str:
    """A number that belongs to both pictures, across the columns."""
    hint = html.escape(HINTS[what.removesuffix(", median")])
    return f'<span class="wide" title="{hint}"><b>{share:.0%}</b> {what}</span>'


def picture(report: Report, result: Result, embed: bool) -> str:
    if result.status != "ok":
        return f'<div class="failed">Not rendered: {html.escape(result.status)}</div>'
    if embed:
        data = base64.b64encode(
            (report.run_directory / result.svg).read_bytes()
        ).decode("ascii")
        source = f"data:image/svg+xml;base64,{data}"
    else:
        source = html.escape(f"{report.name}/{result.svg}")
    return f'<img loading="lazy" alt="" src="{source}">'


def tab(report: Report, options: argparse.Namespace, embed: bool) -> str:
    """One run: Graphviz on the left, the other engine on the right, the numbers between."""
    other = next(variant for variant in report.variants if variant != BASELINE)
    compared = [by_variant for _, by_variant in report.ranked]

    def heading(variant: str) -> str:
        return (
            f"<h2>{VARIANT_TITLES[variant]}<small>{VARIANT_HINTS[variant]}</small></h2>"
        )

    def total(variant: str) -> dict[str, object]:
        fine = [
            by_variant[variant]
            for by_variant in compared
            if by_variant[variant].status == "ok"
        ]
        edges = sum(result.edges for result in fine)
        return {
            "rendered": len(fine),
            "edges": edges,
            "orthogonal": f"{sum(result.orthogonal_edges for result in fine) / max(edges, 1):.0%}",
            "label overlaps": sum(result.label_overlaps for result in fine),
            "seconds": f"{statistics.median([result.seconds for result in fine] or [0]):.1f}",
        }

    left, right = total(BASELINE), total(other)
    pairs = [
        (by_variant[other].crossings, by_variant[BASELINE].crossings)
        for by_variant in compared
        if by_variant[other].status == "ok" and by_variant[BASELINE].status == "ok"
    ]
    fewer = sum(mine < theirs for mine, theirs in pairs)
    more = sum(mine > theirs for mine, theirs in pairs)
    totals = [row(left[what], what, right[what]) for what in left]
    lead = (
        ""
        if fewer == more
        else f'<b class="{"good" if fewer > more else "bad"}">{fewer - more:+d}</b>'
    )
    totals.insert(
        3, row(more, "diagrams with fewer crossings", fewer, lead.replace("-", "−"))
    )
    # Lengths and bends only add up over the diagrams that both engines rendered.
    shared = [
        (by_variant[other], by_variant[BASELINE])
        for by_variant in compared
        if by_variant[other].status == "ok" and by_variant[BASELINE].status == "ok"
    ]
    length, baseline_length = (
        sum(pair[side].edge_length for pair in shared) for side in (0, 1)
    )
    totals.append(
        row(
            f"{baseline_length:.0f}",
            "edge length",
            f"{length:.0f}",
            growth(length, baseline_length),
        )
    )
    if report.name == "ortho":
        bends, baseline_bends = (
            sum(pair[side].bends for pair in shared) for side in (0, 1)
        )
        totals.append(row(baseline_bends, "bends", bends, delta(bends, baseline_bends)))
    for what, shares in (
        ("same arrangement", [alike.arrangement for alike in report.alike.values()]),
        ("same edge sides", [alike.edge_sides for alike in report.alike.values()]),
    ):
        known = [share for share in shares if share is not None]
        if known:
            totals.append(wide(statistics.median(known), what + ", median"))
    summary_card = (
        f'<section class="card summary"><header><span class="name">{RUN_HINTS[report.name]}:'
        f" {len(compared)}</span></header>"
        f'<div class="compare"><div>{heading(BASELINE)}</div><div></div><div>{heading(other)}</div>'
        f'<div class="numbers" style="grid-column: 1 / -1; grid-template-columns: 1fr 300px 1fr">'
        f"{''.join(totals)}</div>"
        "</div></section>"
    )

    cards = []
    for (repo, path), by_variant in report.ranked:
        mine, theirs = by_variant[other], by_variant[BASELINE]
        both = mine.status == "ok" and theirs.status == "ok"
        rows = [
            row(theirs.edges, "edges", mine.edges),
            row(theirs.orthogonal_edges, "orthogonal", mine.orthogonal_edges),
            row(
                theirs.crossings,
                "crossings",
                mine.crossings,
                delta(mine.crossings, theirs.crossings),
            ),
            row(
                theirs.label_overlaps,
                "label overlaps",
                mine.label_overlaps,
                delta(mine.label_overlaps, theirs.label_overlaps),
            ),
            row(
                f"{theirs.edge_length:.0f}",
                "edge length",
                f"{mine.edge_length:.0f}",
                growth(mine.edge_length, theirs.edge_length),
            ),
            *(
                [
                    row(
                        theirs.bends,
                        "bends",
                        mine.bends,
                        delta(mine.bends, theirs.bends),
                    )
                ]
                if report.name == "ortho"
                else []
            ),
            row(
                f"{theirs.width:.0f}×{theirs.height:.0f}",
                "size",
                f"{mine.width:.0f}×{mine.height:.0f}",
            ),
        ]
        alike = report.alike.get((repo, path), Alike())
        rows += [
            wide(share, what)
            for share, what in (
                (alike.arrangement, "same arrangement"),
                (alike.edge_sides, "same edge sides"),
            )
            if share is not None
        ]
        likeness = alike.both()
        changed = (
            not both
            or mine.crossings != theirs.crossings
            or mine.label_overlaps != theirs.label_overlaps
        )
        # How many flaws came on top of the Graphviz ones. A failure outweighs them all.
        if both:
            added = (
                mine.crossings
                + mine.label_overlaps
                - theirs.crossings
                - theirs.label_overlaps
            )
        else:
            added = 1_000_000 if mine.status != "ok" else -1_000_000
        link = f"https://github.com/{options.org}/{repo}/blob/HEAD/{path}"
        cards.append(
            f'<section class="card diagram" data-repository="{html.escape(repo)}"'
            f' data-name="{html.escape((repo + "/" + path).lower())}"'
            f' data-changed="{"yes" if changed else "no"}" data-rank="{len(cards)}"'
            f' data-edges="{max(mine.edges, theirs.edges)}" data-added="{added}"'
            f' data-alike="{1 if likeness is None else likeness:.4f}">'
            f'<header><span class="repository">{html.escape(repo)}</span>'
            f'<span class="name">{html.escape(path)}</span><a href="{html.escape(link)}">source</a></header>'
            f'<div class="compare"><div>{heading(BASELINE)}{picture(report, theirs, embed)}</div>'
            f'<div class="numbers">{"".join(rows) if both else ""}</div>'
            f"<div>{heading(other)}{picture(report, mine, embed)}</div></div></section>"
        )
    return (
        f'<section class="tab" id="{report.name}" hidden>{summary_card}'
        f'<div class="cards">{"".join(cards)}</div></section>'
    )


def page(reports: list[Report], options: argparse.Namespace) -> str:
    """A page to pass on, with one tab per run."""
    size = sum(
        (report.run_directory / result.svg).stat().st_size
        for report in reports
        for _, by_variant in report.ranked
        for result in by_variant.values()
        if result.status == "ok"
    )
    embed = size <= options.embed_limit * 1_000_000
    buttons = "".join(
        f'<button data-tab="{report.name}">{RUN_TITLES[report.name]}'
        f" <small>{len(report.ranked)}</small></button>"
        for report in reports
    )
    repositories = "".join(
        f"<option>{html.escape(repo)}</option>"
        for repo in sorted({key[0] for report in reports for key, _ in report.ranked})
    )
    commit = options.commit.split()[0]
    version = (
        f'<a href="{PLANTUML_REPOSITORY}/commit/{commit}"><code>{commit}</code></a>'
        if re.fullmatch(r"[0-9a-f]{7,40}", commit)
        else f"<code>{html.escape(options.commit)}</code>"
    )
    separate = (
        "" if embed else " The pictures are separate files, pass the whole folder on."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PlantUML: Graphviz or Smetana?</title><style>{STYLE}</style></head>
<body><main>
<h1>PlantUML: Graphviz or Smetana?</h1>
<p class="muted">Diagrams of {html.escape(options.org)} · {options.date} · PlantUML {version} ·
{html.escape(options.graphviz)}</p>
<p>The same diagrams, laid out by two engines. <b class="bad">Red</b> is worse than Graphviz,
<b class="good">green</b> is better. The numbers are estimates read from the pictures.{separate}</p>
<nav class="tabs">{buttons}</nav>
<div class="filters">
<input id="search" type="search" placeholder="Filter by path">
<select id="repository"><option value="">All repositories</option>{repositories}</select>
<label><input id="changed" type="checkbox"> only where the numbers differ</label>
<label>Sort <select id="sort">
<option value="worse">worse than Graphviz first</option>
<option value="better">better than Graphviz first</option>
<option value="different">least alike first</option>
<option value="complex">most edges first</option>
</select></label>
<span id="shown" class="muted"></span>
<span class="muted">Click a picture to enlarge it.</span>
</div>
{"".join(tab(report, options, embed) for report in reports)}
</main>
<div id="zoom"><img alt=""></div>
<script>{SCRIPT}</script>
</body></html>
"""


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", choices=[*RUNS, "both"], default="both")
    parser.add_argument("--org", default="eclipse-score")
    parser.add_argument("--cache", type=Path, help="default: ~/.cache/repo-cache/<org>")
    parser.add_argument(
        "--out", type=Path, default=Path.home() / ".cache" / "plantuml-compare"
    )
    parser.add_argument(
        "--repo", action="append", default=[], help="only this repository, repeatable"
    )
    parser.add_argument(
        "--skip-sync", action="store_true", help="use the repository cache as it is"
    )
    parser.add_argument(
        "--skip-build", action="store_true", help="use the jar built by an earlier run"
    )
    parser.add_argument(
        "--jar", type=Path, help="use this PlantUML jar instead of building one"
    )
    parser.add_argument(
        "--jdk",
        type=Path,
        help="JDK to build and run PlantUML with, default: JAVA_HOME",
    )
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument(
        "--timeout", type=float, default=120, help="seconds per diagram"
    )
    parser.add_argument(
        "--reuse", action="store_true", help="keep the renders of an earlier run"
    )
    parser.add_argument(
        "--embed-limit",
        type=float,
        default=30,
        help="put the pictures into index.html itself up to this many megabytes",
    )
    options = parser.parse_args()

    options.cache = options.cache or Path.home() / ".cache" / "repo-cache" / options.org
    options.out = options.out.expanduser().resolve()
    options.out.mkdir(parents=True, exist_ok=True)
    jdk = options.jdk or (
        Path(os.environ["JAVA_HOME"]) if os.environ.get("JAVA_HOME") else None
    )
    java = str(jdk / "bin" / "java") if jdk else shutil.which("java")
    if java is None:
        sys.exit("No java found. Pass --jdk or set JAVA_HOME.")
    if shutil.which("dot") is None:
        sys.exit("No dot found. The graphviz variant needs Graphviz.")

    if not options.skip_sync:
        sync_cache(options.org)
    if options.jar is not None:
        options.jar = options.jar.expanduser().resolve()
        options.commit = f"{options.jar.name} (given with --jar)"
    elif options.skip_build:
        options.jar = find_jar(options.out / "plantuml-src")
        options.commit = jar_commit(options.out / "plantuml-src")
    else:
        options.jar, options.commit = build_jar(options.out, jdk)

    options.date = datetime.date.today().isoformat()
    version = subprocess.run(["dot", "-V"], capture_output=True, text=True, check=False)
    options.graphviz = (version.stderr or version.stdout).strip().removeprefix("dot - ")

    sources = collect(options.cache, options.repo)
    ortho = {source for source in sources if asks_for_ortho(source)}
    reports = [
        execute(
            name,
            [source for source in sources if (source in ortho) == (name == "ortho")],
            options,
            java,
        )
        for name in (RUNS if options.run == "both" else [options.run])
    ]
    (options.out / "index.html").write_text(page(reports, options))
    print(f"See {options.out / 'index.html'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
