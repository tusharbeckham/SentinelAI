# Hero v5 -- Embedding Space

## Why v4 was scrapped

v4 (The Instrument) was rejected on sight, and the criticism was correct.
Three concrete failures, all visible in one screenshot:

1. **The graticule collided with the subtitle.** `herohud.tsx` drew into a
   fixed `1600x900` viewBox with `preserveAspectRatio="xMidYMid slice"`.
   Slice scales to *cover*, so at any aspect ratio other than 16:9 the box is
   cropped inward and the corner readouts walk into the middle of the screen.
   Readouts pinned at `y=210` and `y=234` landed directly on the line
   "anomaly detection built around the analyst budget".
2. **The rings read as noise.** Faint dotted arcs floating behind live text
