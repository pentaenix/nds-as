"""Categorized Models Resource submission planning for Marine Park Empire."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Callable, Iterable
import zipfile

from PIL import Image

from .submission import SnapshotRenderer, export_submission, submission_title


CATEGORY_FOLDERS = (
    "animals",
    "decor",
    "human",
    "items",
    "show animals",
    "ambient animals",
    "fences",
    "vehicles",
)

_BIND_SUFFIX = re.compile(r"(?:[_-](?:binding|bind))$", re.IGNORECASE)

_ANIMAL_TITLE_OVERRIDES = {
    "bactrian": "Bactrian Camel",
    "bactrians": "Bactrian Camel Baby",
    "bshark": "Blacktip Shark",
    "bsharks": "Blacktip Shark Baby",
    "canth": "Coelacanth",
    "canths": "Coelacanth Baby",
    "eel": "Moray Eel",
    "eels": "Moray Eel Baby",
    "ele_afr": "African Elephant",
    "ele_afrs": "African Elephant Baby",
    "ele_asia": "Asian Elephant",
    "ele_asias": "Asian Elephant Baby",
    "epenguinbaby": "Emperor Penguin Baby",
    "g_tortoise": "Giant Tortoise",
    "g_tortoise_baby": "Giant Tortoise Baby",
    "goctopus": "Giant Pacific Octopus",
    "goctopuss": "Giant Pacific Octopus Baby",
    "hshark": "Hammerhead Shark",
    "hsharks": "Hammerhead Shark Baby",
    "jfish": "Jellyfish",
    "jfishs": "Jellyfish Baby",
    "jfishs#": "Jellyfish Variant",
    "k_dragon": "Komodo Dragon",
    "k_dragons": "Komodo Dragon Baby",
    "kangaros": "Kangaroo Baby",
    "mantaray": "Manta Ray",
    "mantarays": "Manta Ray Baby",
    "narwal": "Narwhal",
    "narwals": "Narwhal Baby",
    "nz_sealion": "New Zealand Sea Lion",
    "oarifish": "Oarfish",
    "oarifishs": "Oarfish Baby",
    "oceanf": "Ocean Sunfish",
    "oceanfs": "Ocean Sunfish Baby",
    "rarecroco": "Rare Crocodile",
    "rarecrocos": "Rare Crocodile Baby",
    "reddeerm": "Red Deer Male",
    "reddeerf": "Red Deer Female",
    "sealion": "Sea Lion",
    "sealion_baby": "Sea Lion Baby",
    "sealionf": "Sea Lion Female",
    "sikam": "Sika Male",
    "wshark": "Great White Shark",
    "wsharks": "Great White Shark Baby",
}


@dataclass(frozen=True, slots=True)
class SubmissionJob:
    descriptor: dict
    category: str
    title: str
    dae_human_t_pose: bool = False


def submission_category(descriptor: dict) -> str | None:
    """Map a catalog descriptor to the requested submission directory."""
    model = descriptor.get("model") or {}
    source = PurePosixPath(str(model.get("path") or ""))
    parent = source.parent.as_posix().casefold()
    stem = source.stem.casefold()
    model_format = str(model.get("format") or "").upper()
    if parent == "model/animal":
        return "animals"
    if parent == "model/decor":
        return "decor"
    if parent == "model/human":
        return "human"
    if parent == "model/train":
        return "vehicles"
    if parent == "model/misc":
        return "ambient animals" if model_format == "AM1" else "vehicles"
    if parent == "model/item":
        if "fence" in stem:
            return "fences"
        if model_format == "AM1" and stem.startswith("show_"):
            return "show animals"
        if model_format == "AM1":
            return "ambient animals"
        return "items"
    # model/Shadow and loose root light/effect records are intentionally skipped.
    return None


def _words(value: str) -> str:
    value = re.sub(r"[_-]+", " ", value)
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", value)
    value = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", value)
    return " ".join(value.split())


def _number(value: str) -> str:
    return f" {value}" if value else ""


def human_submission_title(model_path: str) -> str:
    """Expand the visitor abbreviations used by the source human catalog."""
    stem = _BIND_SUFFIX.sub("", PurePosixPath(model_path).stem.strip())
    folded = stem.casefold()
    if folded == "vomnormal01":
        return "Visitor Old Normal 01"
    patterns = (
        (r"VAF(Normal|Tall)(\d*)", "Visitor Adult Female"),
        (r"VAMale(Normal|Tall)(\d*)", "Visitor Adult Male"),
        (r"VAM(Normal|Tall)(\d*)", "Visitor Adult Male"),
        (r"VKFM(Short)(\d*)", "Visitor Kid Female"),
        (r"VKF(Short)(\d*)", "Visitor Kid Female"),
        (r"VKM(Short)(\d*)", "Visitor Kid Male"),
        (r"VOFN(\d+)", "Visitor Old Female Normal"),
    )
    for expression, prefix in patterns:
        match = re.fullmatch(expression, stem, re.IGNORECASE)
        if match is None:
            continue
        if expression.startswith("VOFN"):
            number = match.group(1)
            if len(number) == 3 and number.startswith("1"):
                number = number[1:]
            return f"{prefix}{_number(number)}"
        build = match.group(1).capitalize()
        return f"{prefix} {build}{_number(match.group(2))}"
    if folded.startswith("vistoradult"):
        stem = "VisitorAdult" + stem[len("VistorAdult"):]
    suffix = ""
    if len(stem) > 1 and stem.endswith("F"):
        stem = stem[:-1]
        suffix = " Female"
    words = _words(stem)
    title = " ".join(word[:1].upper() + word[1:] for word in words.split()) or "Human"
    return f"{title}{suffix}"


def readable_submission_title(descriptor: dict) -> str:
    model_path = str((descriptor.get("model") or {}).get("path") or "model")
    category = submission_category(descriptor)
    if category == "human":
        return human_submission_title(model_path)
    if category == "animals":
        override = _ANIMAL_TITLE_OVERRIDES.get(PurePosixPath(model_path).stem.casefold())
        if override:
            return override
    title = submission_title(model_path)
    return re.sub(r"(?<=[A-Za-z])(?=\d)", " ", title)


def _descriptor_preference(descriptor: dict) -> tuple[int, int, int]:
    model = descriptor.get("model") or {}
    stem = PurePosixPath(str(model.get("path") or "")).stem
    is_bind = bool(_BIND_SUFFIX.search(stem))
    is_expanded = stem.casefold().startswith("visitor") or stem.casefold().startswith("vistor")
    return (0 if is_bind else 100, 20 if is_expanded else 0, int(model.get("size") or 0))


def build_submission_jobs(descriptors: Iterable[dict]) -> list[SubmissionJob]:
    """Build deterministic, title-deduplicated jobs from scanned model rows."""
    chosen: dict[tuple[str, str], dict] = {}
    for descriptor in descriptors:
        category = submission_category(descriptor)
        if category is None:
            continue
        title = readable_submission_title(descriptor)
        key = category.casefold(), title.casefold()
        current = chosen.get(key)
        if current is None or _descriptor_preference(descriptor) > _descriptor_preference(current):
            chosen[key] = descriptor
    # Some working/binding files spell an otherwise identical visitor variant
    # with a trailing 01 that is absent from its finished model name.
    for key, descriptor in list(chosen.items()):
        stem = PurePosixPath(str((descriptor.get("model") or {}).get("path") or "")).stem
        if _BIND_SUFFIX.search(stem) and key[1].endswith(" 01"):
            without_number = (key[0], key[1][:-3])
            if without_number in chosen:
                del chosen[key]
    jobs = [
        SubmissionJob(
            descriptor=descriptor,
            category=category,
            title=readable_submission_title(descriptor),
            dae_human_t_pose=category == "human",
        )
        for (category, _), descriptor in chosen.items()
    ]
    return sorted(jobs, key=lambda job: (CATEGORY_FOLDERS.index(job.category), job.title.casefold()))


def submission_complete(directory: Path, title: str) -> bool:
    """Perform cheap resume checks before an expensive decode and render."""
    directory = Path(directory)
    package = directory / f"{title}.zip"
    icon = directory / f"{title}_icon.png"
    glb = directory / f"{title}_preview.glb"
    preview = directory / f"{title}_preview.png"
    try:
        with zipfile.ZipFile(package) as archive:
            if archive.testzip() is not None or not any(
                name.casefold().endswith(".dae") for name in archive.namelist()
            ):
                return False
        with Image.open(icon) as image:
            if image.size != (148, 125) or image.mode != "RGBA":
                return False
        with Image.open(preview) as image:
            if image.size != (750, 650) or image.mode != "RGBA":
                return False
        return glb.read_bytes()[:4] == b"glTF"
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def export_submission_jobs(
    jobs: Iterable[SubmissionJob],
    output_root: Path,
    snapshot_renderer: SnapshotRenderer,
    *,
    force: bool = False,
    report_filename: str = "bulk_export_report.json",
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Export a resumable batch and keep a machine-readable report."""
    jobs = list(jobs)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    report: dict = {"total": len(jobs), "exported": [], "skipped": [], "errors": []}
    report_path = output_root / report_filename
    for index, job in enumerate(jobs, 1):
        target = output_root / job.category
        target.mkdir(parents=True, exist_ok=True)
        if not force and submission_complete(target, job.title):
            report["skipped"].append({"category": job.category, "title": job.title})
            if progress:
                progress(f"[{index}/{len(jobs)}] Already complete: {job.category}/{job.title}")
        else:
            if progress:
                progress(f"[{index}/{len(jobs)}] Exporting {job.category}/{job.title}")
            try:
                export_submission(
                    job.descriptor,
                    target,
                    snapshot_renderer,
                    title_override=job.title,
                    dae_human_t_pose=job.dae_human_t_pose,
                    progress=progress,
                )
                report["exported"].append({"category": job.category, "title": job.title})
            except Exception as exc:
                report["errors"].append({
                    "category": job.category,
                    "title": job.title,
                    "error": str(exc),
                })
                if progress:
                    progress(f"Failed {job.category}/{job.title}: {exc}")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
