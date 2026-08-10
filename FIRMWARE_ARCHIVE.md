# Neato XV firmware archive and recovery record

The working archive lives outside the Git repository on the 2 TB volume:

```text
/Volumes/2TB/neato-firmware-archive/
├── sources/Neato-XV-Series-Cruz-Rev-113-Update/
├── analysis/
│   ├── SHA256SUMS.all
│   ├── catalog.json
│   └── cruz-p-images.json
└── robot-backups/
    └── WTD41611DD-0037829-P_sw-2-4-15667_20260810T001304Z/
```

`SHA256SUMS.all` records every archived source file. The robot snapshot has its
own `SHA256SUMS` covering the JSON record and raw transcript. It preserves the
installed version identity, calibration, schedule, warranty, charger state,
sensors, and command help without changing flash. It is **not** the installed
application binary: stock firmware 2.4 does not export that region over USB.

## Archived Cruz application images

| Release | Build | Hardware suffix | Encrypted file size | Declared plaintext |
|---|---:|---|---:|---:|
| 2.5 | 15893 | P | 805,888 | 805,156 |
| 2.7 | 16621 | P | 805,888 | 805,284 |
| 3.1 | 17844 | P | 847,872 | 847,100 |
| 3.2 | 18755 | P | 852,992 | 851,984 |

The P images are the relevant family for the current robot (`...P`, mainboard
7.1 / Cruz Rev113). Version 3.1 is the initial patch-analysis target because it
is the mature public Cruz update commonly associated with Rev113 hardware. It
must not be flashed merely because it is compatible: first unlock it, validate
the plaintext, identify all flash regions, and prove a recovery/readback path.

The upstream source archive is
[NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update](https://github.com/NoahJaehnert/Neato-XV-Series-Cruz-Rev-113-Update).
The protocol reference is Neato's
[XV Programmer's Manual](https://help.neatorobotics.com/wp-content/uploads/2020/07/XV-ProgrammersManual-3_1.pdf).

## Reproduce the records

Create another read-only snapshot (logs are omitted because 2.4 may reset its
USB connection while reading them):

```bash
python3 backup_neato.py
```

Rebuild the archive catalog or inspect an encrypted image:

```bash
python3 neato_firmware.py catalog \
  /Volumes/2TB/neato-firmware-archive/sources/Neato-XV-Series-Cruz-Rev-113-Update \
  --output /Volumes/2TB/neato-firmware-archive/analysis/catalog.json

python3 neato_firmware.py inspect /path/to/XV11App.bin.enc
python3 neato_firmware.py validate-unlock /path/to/XV11App.bin.enc /path/to/plaintext.bin
```

`validate-unlock` exits with status 2 when the proposed plaintext does not pass
the structural checks. If a decryptor also emits a reconstructed encrypted
file, pass it with `--repacked`; exact SHA-256 equality then becomes mandatory.
