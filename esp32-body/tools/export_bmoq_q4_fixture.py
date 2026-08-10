#!/usr/bin/env python3
"""Write a deterministic, streaming grouped-Q4 BMOQ fixture.

The default weight tensor is larger than 8 MiB. Rows are generated and written
one at a time, so converter memory is O(columns), not O(model size).
"""

import argparse
import struct
from pathlib import Path

HEADER_BYTES = 4096
ALIGNMENT = 4096


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def qvalue(row: int, column: int, group_size: int) -> int:
    return (row * 3 + column * 5 + (column // group_size) * 7) % 16 - 8


def scale(row: int, group: int) -> float:
    return (1 + (row + group) % 7) * 0.01


def entry(tensor_id: int, dtype: int, group: int, rows: int, columns: int,
          offset: int, length: int, layout: int, name: str) -> bytes:
    encoded = name.encode("ascii")[:16].ljust(16, b"\0")
    return struct.pack("<IHH4IQQII16s", tensor_id, dtype, group, rows, columns,
                       1, 1, offset, length, layout, 0, encoded)


def write_fixture(path: Path, rows: int, columns: int, group_size: int) -> None:
    if group_size < 2 or group_size % 2 or columns % group_size:
        raise ValueError("group size must be even and divide columns")
    groups = columns // group_size
    weight_offset = HEADER_BYTES * 2
    weight_bytes = rows * columns // 2
    scale_offset = align_up(weight_offset + weight_bytes, ALIGNMENT)
    scale_bytes = rows * groups * 4
    header = bytearray(HEADER_BYTES)
    struct.pack_into("<4sH2x4I Q", header, 0, b"BMOQ", 1, 0x01020304,
                     HEADER_BYTES, 2, 64, HEADER_BYTES)
    directory = b"".join((
        entry(100, 2, group_size, rows, columns, weight_offset, weight_bytes, 1,
              "q4.weight"),
        entry(101, 1, group_size, rows, groups, scale_offset, scale_bytes, 2,
              "q4.scale"),
    ))
    with path.open("wb") as output:
        output.write(header)
        output.write(directory)
        output.seek(weight_offset)
        packed = bytearray(columns // 2)
        for row in range(rows):
            for column in range(0, columns, 2):
                low = qvalue(row, column, group_size) & 0xF
                high = qvalue(row, column + 1, group_size) & 0xF
                packed[column // 2] = low | high << 4
            output.write(packed)
        output.seek(scale_offset)
        for row in range(rows):
            for group in range(groups):
                output.write(struct.pack("<f", scale(row, group)))
    print(f"{path}: weights={weight_bytes} scales={scale_bytes} total={path.stat().st_size}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=40_000)
    parser.add_argument("--columns", type=int, default=448)
    parser.add_argument("--group-size", type=int, default=32)
    args = parser.parse_args()
    write_fixture(args.output, args.rows, args.columns, args.group_size)


if __name__ == "__main__":
    main()
