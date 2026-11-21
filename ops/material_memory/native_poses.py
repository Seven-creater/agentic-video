"""Time existing drawings to a locked voice recording, keeping each source frame intact."""

import itertools

import numpy as np
from scipy.ndimage import maximum_filter1d, median_filter, minimum_filter1d
from scipy.spatial.distance import cdist


def openings(samples, first, length, fps=30, rate=48000):
    """Estimate open/closed poses from the local syllable envelope of the isolated voice."""
    edges = np.rint(np.arange(first, first + length + 1) * rate / fps).astype(int)
    if length < 1 or edges[0] < 0:
        raise ValueError("Invalid line duration")
    energy = np.array(
        [
            np.sqrt(np.mean(samples[a:b].astype(float) ** 2))
            if a < len(samples) and b > a
            else 0
            for a, b in itertools.pairwise(edges)
        ]
    )
    peak = np.percentile(energy, 90)
    if peak < 50:
        return np.zeros(length)
    energy = median_filter(energy, size=3, mode="nearest")
    high = maximum_filter1d(energy, size=9, mode="nearest")
    low = minimum_filter1d(energy, size=9, mode="nearest")
    gate = np.maximum(peak * 0.10, low + 0.45 * (high - low))
    return ((energy >= gate) & (energy > peak * 0.06)).astype(float)


def frame_sequence(frames, closed, opened, mouth, targets):
    """Find a path through native source frames. Prefer forward playback and visually matched joins."""
    x, y, width, height = mouth
    h, w = frames.shape[1:3]
    box = (
        slice(max(0, round(y - height * 0.8 - 2)), min(h, round(y + height * 0.8 + 3))),
        slice(max(0, round(x - width * 0.7 - 2)), min(w, round(x + width * 0.7 + 3))),
    )
    lips = frames[:, box[0], box[1]].astype(float)

    def difference(ref):
        return np.abs(lips - lips[ref]).mean(axis=(1, 2, 3))

    c, a = difference(closed), difference(opened)
    if c[opened] < 2.0:
        raise ValueError(
            "Two mouth poses cannot be distinguished at the supplied location"
        )
    openness = c / np.maximum(0.01, c + a)
    n = len(frames)
    # Evaluate changes beyond the mouth separately. A jump between similar backgrounds
    # can still jerk if the head or the eyes have moved.
    small = frames[:, ::10, ::10].astype(np.float32)
    xa, xb = (
        max(0, int((x - width * 2.8) // 10)),
        min(small.shape[2], int((x + width * 2.8) // 10) + 1),
    )
    ya, yb = (
        max(0, int((y - width * 2.2) // 10)),
        min(small.shape[1], int((y + width) // 10) + 1),
    )
    face = small[:, ya:yb, xa:xb].copy()
    face[
        :,
        max(0, box[0].start // 10 - ya) : max(0, box[0].stop // 10 + 1 - ya),
        max(0, box[1].start // 10 - xa) : max(0, box[1].stop // 10 + 1 - xa),
    ] = 0

    def distances(values):
        flat = values.reshape(n, -1)
        return cdist(flat, flat, metric="cityblock") / max(1, flat.shape[1])

    i = np.arange(n)
    step = i[None, :] - i[:, None]
    jump = np.where(
        step == 1,
        0,
        np.where(step == 0, 0.12, 0.33 + np.minimum(np.abs(step - 1), 35) * 0.009),
    )
    continuity = np.where(
        step == 1, 0, (distances(small) * 0.022 + distances(face) * 0.048)
    )
    transitions = jump + continuity
    errors = 2.7 * np.abs(targets[:, None] - openness)
    costs = errors[0]
    parents = np.zeros((len(targets), n), np.int32)
    for t in range(1, len(targets)):
        options = costs[:, None] + transitions
        parents[t] = np.argmin(options, axis=0)
        costs = options[parents[t], i] + errors[t]
    path = [int(np.argmin(costs))]
    for t in range(len(targets) - 1, 0, -1):
        path.append(int(parents[t, path[-1]]))
    path = path[::-1]
    return path, openness[path].round(3).tolist()


def frame_crop(mouth, framing, width=640, height=360):
    if framing == "full":
        return [0.0, 0.0, 1.0]
    x, y, w, _ = mouth
    size = min(1.0, max(0.33, w / width * (15.5 if framing == "medium" else 12.0)))
    left = max(0, min(1 - size, x / width - size * 0.50))
    top = max(0, min(1 - size, y / height - size * 0.68))
    return [left, top, size]
