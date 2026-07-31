"""Pinned, headless SPICA bridge used for NintendoWare CGFX conversion.

The bridge is built lazily into ``exports/.tooling``.  No SPICA code is
imported by RAE, and Pokémon's native GFModel path never calls this module.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from ...install import project_root


SPICA_REPOSITORY = "https://github.com/KillzXGaming/SPICA.git"
SPICA_COMMIT = "505e8d01e0e80918ba972b45b6642e25e3daa755"

_PROJECT = r'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <RollForward>Major</RollForward>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <ProjectReference Include="../spica/SPICA/SPICA.csproj" />
  </ItemGroup>
</Project>
'''

_PROGRAM = r'''using SPICA.Formats.CtrGfx;
using SPICA.Formats.CtrH3D;
using SPICA.Formats.Generic.COLLADA;
using SixLabors.ImageSharp;
using System.Text.Json;

if (args.Length < 2) {
    Console.Error.WriteLine("usage: Rae.CgfxBridge inspect INPUT | export OUTPUT.dae INPUT...");
    return 2;
}

try {
    if (args[0] == "inspect") {
        H3D scene = Gfx.Open(args[1]).ToH3D();
        Console.WriteLine(JsonSerializer.Serialize(new {
            models = scene.Models.Select(x => x.Name).ToArray(),
            textures = scene.Textures.Select(x => x.Name).ToArray(),
            skeletalAnimations = scene.SkeletalAnimations.Select(x => x.Name).ToArray(),
            materialAnimations = scene.MaterialAnimations.Select(x => x.Name).ToArray()
        }));
        return 0;
    }
    if (args[0] != "export" || args.Length < 3) return 2;
    string output = Path.GetFullPath(args[1]);
    H3D merged = Gfx.Open(args[2]).ToH3D();
    foreach (string input in args.Skip(3)) merged.Merge(Gfx.Open(input).ToH3D());
    if (merged.Models.Count == 0) throw new InvalidDataException("CGFX input contains no model");
    int animationIndex = merged.SkeletalAnimations.Count > 0 ? 0 : -1;
    new DAE(merged, 0, animationIndex).Save(output);
    string directory = Path.GetDirectoryName(output)!;
    foreach (var texture in merged.Textures) {
        using var image = texture.ToBitmap();
        image.SaveAsPng(Path.Combine(directory, texture.Name + ".png"));
    }
    Console.WriteLine(JsonSerializer.Serialize(new {
        model = merged.Models[0].Name,
        textures = merged.Textures.Count,
        skeletalAnimations = merged.SkeletalAnimations.Count,
        selectedAnimation = animationIndex >= 0 ? merged.SkeletalAnimations[0].Name : null
    }));
    return 0;
} catch (Exception ex) {
    Console.Error.WriteLine(ex.ToString());
    return 1;
}
'''


class CgfxToolError(RuntimeError):
    pass


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        message = (result.stderr or result.stdout or "command failed").strip()
        raise CgfxToolError(message)
    return result


def ensure_cgfx_bridge(progress: Callable[[str], None] | None = None) -> Path:
    report = progress or (lambda _message: None)
    dotnet = shutil.which("dotnet")
    git = shutil.which("git")
    if not dotnet or not git:
        raise CgfxToolError("CGFX preview requires both git and the .NET SDK (8 or newer).")
    root = project_root() / "exports" / ".tooling" / "cgfx_bridge"
    spica = root / "spica"
    bridge = root / "bridge"
    output_dir = root / "bin-v2"
    output = output_dir / "Rae.CgfxBridge.dll"
    if output.is_file():
        return output
    root.mkdir(parents=True, exist_ok=True)
    if not (spica / ".git").is_dir():
        report("3DS CGFX: downloading the pinned SPICA parser…")
        _run([git, "clone", "--filter=blob:none", "--no-checkout", SPICA_REPOSITORY, str(spica)])
    report("3DS CGFX: selecting the verified SPICA revision…")
    _run([git, "fetch", "--depth", "1", "origin", SPICA_COMMIT], cwd=spica)
    _run([git, "checkout", "--detach", SPICA_COMMIT], cwd=spica)
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / "Rae.CgfxBridge.csproj").write_text(_PROJECT, encoding="utf-8")
    (bridge / "Program.cs").write_text(_PROGRAM, encoding="utf-8")
    report("3DS CGFX: building the headless conversion bridge…")
    _run(
        [
            dotnet,
            "build",
            str(bridge / "Rae.CgfxBridge.csproj"),
            "--configuration",
            "Release",
            "--output",
            str(output_dir),
            "--nologo",
        ]
    )
    if not output.is_file():
        raise CgfxToolError("The CGFX bridge build completed without producing its executable.")
    return output


def run_cgfx_bridge(
    arguments: list[str | Path],
    *,
    progress: Callable[[str], None] | None = None,
) -> str:
    bridge = ensure_cgfx_bridge(progress)
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        raise CgfxToolError("The .NET SDK is no longer available.")
    result = _run([dotnet, str(bridge), *(str(value) for value in arguments)])
    return result.stdout.strip()
