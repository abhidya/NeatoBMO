"""Sound vocabulary harvested from the XV-12 (PlaySound 0-20)."""

SOUNDS = {
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

# emotional aliases for the buddy persona
MOODS = {
    "happy": "cleaning_completed",
    "hello": "waking_up",
    "curious": "exploring",
    "scared": "picked_up",
    "sad": "going_to_sleep",
    "grateful": "thank_you",
    "alarm": "alert",
    "protest": "user_canceled",
}
