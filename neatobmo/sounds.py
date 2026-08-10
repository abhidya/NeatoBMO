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
SOUNDS = {
    name: sound_id
    for name, sound_id in DOCUMENTED_SOUNDS.items()
    if sound_id in LIVE_SOUND_IDS
}

# emotional aliases for the buddy persona
MOODS = {
    "happy": "cleaning_completed",
    "hello": "waking_up",
    "curious": "test1",
    "scared": "attention_needed",
    "sad": "attention_needed",
    "grateful": "cleaning_completed",
    "alarm": "alert",
    "protest": "attention_needed",
}
