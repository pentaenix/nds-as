"""Parse building model placements from Gen 4 land_data map files."""
from __future__ import annotations

import struct

BUILDING_ENTRY_SIZE = 0x28


def parse_land_data_building_model_indices(data: bytes) -> list[int]:
    """Extract build_model indices from the building section of a land_data file."""
    if len(data) < 24:
        return []
    try:
        section_sizes = struct.unpack_from("<6I", data, 0)
    except struct.error:
        return []
    offset = 24
    if len(section_sizes) < 3:
        return []
    offset += section_sizes[0]
    offset += section_sizes[1]
    build_size = section_sizes[2]
    if build_size <= 0 or offset + build_size > len(data):
        return []
    build_data = data[offset:offset + build_size]
    models: list[int] = []
    for entry_offset in range(0, len(build_data), BUILDING_ENTRY_SIZE):
        if entry_offset + 4 > len(build_data):
            break
        model_id = struct.unpack_from("<I", build_data, entry_offset)[0]
        if model_id == 0xFFFFFFFF:
            continue
        if model_id > 5000:
            continue
        models.append(model_id)
    return models
