# coli_mcu host checks

Run the portable BMOQ parser and tiled-read test from `esp32-body`:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -Wall -Wextra -Werror \
  -Icomponents/coli_mcu/include \
  components/coli_mcu/model_format.c components/coli_mcu/store_file.c \
  tools/test_bmoq.c -o /tmp/test_bmoq && /tmp/test_bmoq
```

The fixture contains a deterministic 16 MiB tensor and the test reads only
small tiles at distant offsets, including a tile crossing a 4 KiB boundary.

Run the streamed Q4 golden test:

```sh
cc -std=c11 -D_FILE_OFFSET_BITS=64 -D_POSIX_C_SOURCE=200809L \
  -Wall -Wextra -Werror -Icomponents/coli_mcu/include \
  components/coli_mcu/model_format.c components/coli_mcu/store_file.c \
  components/coli_mcu/q4_matvec.c tools/test_q4_matvec.c -lm \
  -o /tmp/test_q4_matvec && /tmp/test_q4_matvec
```

It streams a deterministic Q4 matrix whose packed weights exceed 8 MiB,
compares all 40,000 output rows with an independent formula reference, and
asserts a 257-byte caller workspace plus cross-4-KiB reads. To retain a fixture
for device testing, run `python3 tools/export_bmoq_q4_fixture.py model.bmoq`.
