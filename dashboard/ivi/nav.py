"""Position and heading along a looping route, from distance travelled."""
from __future__ import annotations

import bisect
import math


class Route:
    def __init__(self, points, cumulative):
        self.points, self.cumulative, self.length = points, cumulative, cumulative[-1]

    @classmethod
    def from_flat(cls, flat):
        points = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        if len(points) < 2:
            raise ValueError('a route needs at least two points')
        cumulative = [0.0]
        for a, b in zip(points, points[1:]):
            cumulative.append(cumulative[-1] + math.dist(a, b))
        if cumulative[-1] <= 0:
            raise ValueError('route has zero length')
        return cls(points, cumulative)


def position(route: Route, distance: float):
    """(x, y, heading): metres east/north and degrees clockwise from north; the loop wraps around."""
    d = distance % route.length
    i = min(max(bisect.bisect_right(route.cumulative, d) - 1, 0), len(route.points) - 2)
    (x0, y0), (x1, y1) = route.points[i], route.points[i + 1]
    span = route.cumulative[i + 1] - route.cumulative[i]
    t = 0.0 if span == 0 else (d - route.cumulative[i]) / span
    return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
