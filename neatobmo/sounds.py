"""Sound vocabulary for the attached XV-12.

The public protocol documents IDs 0--20, but firmware 2.4.15667 on this robot
only accepts a ten-slot subset.  Keep the documentation map separately so the
runtime never silently sends a known out-of-range sound command.
"""

DOCUMENTED_SOUNDS = {
    "waking_up": 0,
    "starting_cleaning": 1,
    "cleaning_completed": 2,
    "attention_needed": 3,
    "backing_into_base": 4,
    "docking_completed": 5,
    "test1": 6,
    "test2": 7,
    "test3": 8,
    "test4": 9,
    "test5": 10,
    "exploring": 11,
    "shutdown": 12,
    "picked_up": 13,
    "going_to_sleep": 14,
    "returning_home": 15,
    "user_canceled": 16,
    "user_terminated": 17,
    "slipped_off_base": 18,
    "alert": 19,
    "thank_you": 20,
}

# Verified by direct USB sweep on WTD41611DD-0037829-P, 2026-08-10.
LIVE_SOUND_IDS = frozenset({0, 1, 2, 3, 6, 7, 8, 9, 10, 19})

BMO_BANK = {
    "name": "DfltSoundLib.BMO.pcm-only.Rev1.0.bin",
    "sha256": "9d3d82d9275c03fa9f2abb163cdfd9393445737999916f6337d2d6b639b51159",
    "bytes": 770048,
    "sample_rate_hz": 22050,
    "format": "signed 16-bit little-endian mono PCM",
    "installed_on": "XV-12 WTD41611DD-0037829-P / firmware 2.4.15667",
}

BMO_SOUND_SLOTS = {
    0: {"key": "video_games_1", "label": "BMO Video Games · 1/3",
        "role": "Waking up / sequence start", "content_seconds": 2.740136,
        "slot_seconds": 2.881995, "source": "BMO Video Games",
        "source_url": "https://www.myinstants.com/en/instant/bmo-video-games-96304/"},
    1: {"key": "video_games_2", "label": "BMO Video Games · 2/3",
        "role": "Starting cleaning / sequence middle", "content_seconds": 1.799819,
        "slot_seconds": 1.867392, "source": "BMO Video Games",
        "source_url": "https://www.myinstants.com/en/instant/bmo-video-games-96304/"},
    2: {"key": "video_games_3", "label": "BMO Video Games · 3/3",
        "role": "Cleaning complete / sequence end", "content_seconds": 0.916735,
        "slot_seconds": 1.869388, "source": "BMO Video Games",
        "source_url": "https://www.myinstants.com/en/instant/bmo-video-games-96304/"},
    3: {"key": "yeah_reaction", "label": "Yeah BMO",
        "role": "Attention reaction", "content_seconds": 0.3,
        "slot_seconds": 0.321451, "source": "Yeah BMO",
        "source_url": "https://www.myinstants.com/en/instant/yeah-bmo-47447/"},
    6: {"key": "bmo_time", "label": "It's BMO Time",
        "role": "Playful announcement", "content_seconds": 1.930023,
        "slot_seconds": 2.008662, "source": "its bmo time",
        "source_url": "https://www.myinstants.com/en/instant/its-bmo-time-1369/"},
    7: {"key": "hello", "label": "Well Hello There",
        "role": "Greeting", "content_seconds": 1.7,
        "slot_seconds": 2.017324, "source": "BMO_Hello",
        "source_url": "https://www.myinstants.com/en/instant/bmo-hello-72979/"},
    8: {"key": "bmobutt", "label": "It goes in my butt",
        "role": "Onboard SSD / vacuum-body storage response", "content_seconds": 1.950023,
        "slot_seconds": 2.008662, "source": "BMObutt",
        "source_url": "https://www.myinstants.com/en/instant/bmobutt-65338/"},
    9: {"key": "json_bmon", "label": "Json Bmon",
        "role": "Surprised reaction", "content_seconds": 1.32,
        "slot_seconds": 2.008662, "source": "Json Bmon",
        "source_url": "https://www.myinstants.com/en/instant/json-bmon-68510/"},
    10: {"key": "homeboys", "label": "BMO Homeboys",
         "role": "Buddy response", "content_seconds": 1.98,
         "slot_seconds": 1.991338, "source": "BMO Homeboys",
         "source_url": "https://www.myinstants.com/en/instant/bmo-homeboys-64616/"},
    19: {"key": "short_reaction", "label": "Json Bmon · Short",
         "role": "Alert reaction", "content_seconds": 0.3,
         "slot_seconds": 0.321451, "source": "Json Bmon",
         "source_url": "https://www.myinstants.com/en/instant/json-bmon-68510/"},
}

BMO_SEQUENCES = {
    "bmo_video_games_burst": {
        "label": "BMO Video Games",
        "ids": [0, 1, 2],
        "description": "Three-part complete Video Games line",
        "playback": "wait for each slot duration before the next command",
    },
    "bmo_all_slots_demo_burst": {
        "label": "Play All BMO Sounds",
        "ids": [0, 1, 2, 6, 7, 8, 9, 10, 3, 19],
        "description": "Every installed BMO slot",
        "playback": "wait for each slot duration before the next command",
    },
    "bmo_short_reactions_burst": {
        "label": "Short Reactions",
        "ids": [3, 19],
        "description": "Two compact reaction clips",
        "playback": "wait for each slot duration before the next command",
    },
}

SOUNDS = {slot["key"]: sound_id for sound_id, slot in BMO_SOUND_SLOTS.items()}
# Retain the old event vocabulary for callers that use Neato event names.
SOUNDS.update({name: sound_id for name, sound_id in DOCUMENTED_SOUNDS.items()
               if sound_id in LIVE_SOUND_IDS})

# emotional aliases for the buddy persona
MOODS = {
    "happy": "yeah_reaction",
    "hello": "hello",
    "curious": "json_bmon",
    "scared": "short_reaction",
    "sad": "short_reaction",
    "grateful": "homeboys",
    "alarm": "short_reaction",
    "protest": "yeah_reaction",
}
