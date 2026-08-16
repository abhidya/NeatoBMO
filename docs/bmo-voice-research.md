# BMO voice research and runtime policy

Research checked 2026-08-16.

## Character and performance source of truth

Warner Bros. Discovery identifies BMO as played by Niki Yang and describes the
character as sassy, adventurous, heroic, and loyal to new friends:

- https://press.wbd.com/us/bio/bmo?language_content_entity=en
- https://press.wbd.com/emea/ca/bio/niki-yang

The OS should therefore prefer the performed character—short, playful,
emotionally direct lines—over making a general TTS engine read long assistant
prose in a similar timbre.

## Catalog findings

The third-party 101soundboards BMO board currently reports 575 entries and
links additional game, episode, and AI-voice boards:

- https://www.101soundboards.com/boards/145834-bmo-soundboard

That count is discovery evidence, not blanket redistribution permission. The
OS catalog records per-item provenance and verification instead of treating
every search result as equally trustworthy.

The prepared repository catalog contains 230 entries (224 unique) across 36
SHA-256-addressed Neato modules:

- 197 verified from the official Cartoon Network Beemo app source set;
- 23 indexed from dedicated BMO board metadata; 21 remain quarantined and two
  human-reviewed clips have had their non-BMO lead-ins removed;
- 10 existing manually reviewed clips.

See `docs/bmo-soundboard/catalog.json`.

## Runtime ladder

1. An exact transcript/alias match selects an authoritative or human-approved
   catalog clip. Metadata-only imports cannot speak automatically.
2. Prompt-time retrieval gives Colibri a small relevant set of exact recorded
   lines instead of injecting the entire catalog into every request.
3. ESP32 `/speak` extracts and relays that clip without a sound-bank flash.
4. Spoken stage cues use the instant-reaction bank and suppress generated
   speech. Decorative beeps do not suppress factual words.
5. Only an unmatched sentence reaches the neural BMO voice model.
6. Colibri espeak and local espeak-ng are survival fallbacks.

Matching stays conservative. A wrong television line is worse than a clearly
synthetic fallback, so the resolver ignores punctuation and catalog prefixes
but does not use loose semantic similarity.

`docs/bmo-clip-approvals.json` is the listening-review ledger.
Adding a metadata-only key to `approved` admits that individual clip; adding it
to `rejected` prevents use even if another verification label is present.

## Storage boundary

The Neato flash bank holds ten simultaneously available reaction slots. The
host catalog holds hundreds of lines. Large-library playback uses runtime WAV
relay where patched firmware supports `PlaySound File`; module installation is
reserved for firmware without that path and for curated offline pages.

## Diagnostics

- `GET /voice/catalog?q=<request>` reports catalog size, exact resolution, and
  prompt-time suggestions without exposing filesystem paths. It separately
  reports trusted and quarantined counts.
- `GET /voice/clip?text=<exact line>` returns the extracted 22050 Hz mono PCM
  WAV used by the runtime, so the browser audits the same artifact BMO plays.
