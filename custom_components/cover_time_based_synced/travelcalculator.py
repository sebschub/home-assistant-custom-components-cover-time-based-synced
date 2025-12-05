"""
Based on
https://github.com/XKNX/xknx/blob/0.9.4/xknx/devices/travelcalculator.py
Module TravelCalculator provides functionality for predicting the current position of a Cover.
Supports multi-segment travel with different speeds for different portions of movement.
E.g.:
* Given a Cover that takes 100 seconds to travel from top to bottom.
* Starting from position 90, directed to position 60 at time 0.
* At time 10 TravelCalculator will return position 80 (final position not reached).
* At time 20 TravelCalculator will return position 70 (final position not reached).
* At time 30 TravelCalculator will return position 60 (final position reached).
"""

import time
from enum import Enum


class PositionType(Enum):
    """Enum class for different type of calculated positions."""

    UNKNOWN = 1
    CALCULATED = 2
    CONFIRMED = 3


class TravelStatus(Enum):
    """Enum class for travel status."""

    DIRECTION_UP = 1
    DIRECTION_DOWN = 2
    STOPPED = 3


class TravelCalculator:
    """Class for calculating the current position of a cover."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        travel_time_down=None,
        travel_time_up=None,
        segments_up=None,
        segments_down=None,
    ):
        """Initialize TravelCalculator class.

        Args:
            travel_time_down: Total time to travel down (used if segments_down not provided)
            travel_time_up: Total time to travel up (used if segments_up not provided)
            segments_up: List of tuples [(position_end, duration), ...] defining segments from 0 to 100 for upward travel.
            segments_down: List of tuples [(position_end, duration), ...] defining segments from 0 to 100 for downward travel.
        """
        self.position_type = PositionType.UNKNOWN
        self.last_known_position = 0

        self.travel_time_down = travel_time_down
        self.travel_time_up = travel_time_up

        self.travel_to_position = 0
        self.travel_started_time = 0
        self.travel_direction = TravelStatus.STOPPED

        # 0 is closed, 100 is fully open
        self.position_closed = 0
        self.position_open = 100

        self.time_set_from_outside = None

        # Set up segments - symmetric definition: both go from 0 to 100 in position
        if segments_up is not None:
            self.segments_up = segments_up
        elif travel_time_up is not None:
            self.segments_up = [(100, travel_time_up)]
        else:
            raise ValueError("Either segments_up or travel_time_up must be provided")

        if segments_down is not None:
            self.segments_down = segments_down
        elif travel_time_down is not None:
            self.segments_down = [(100, travel_time_down)]
        else:
            raise ValueError(
                "Either segments_down or travel_time_down must be provided"
            )

    def set_position(self, position):
        """Set known position of cover."""
        self.last_known_position = position
        self.travel_to_position = position
        self.position_type = PositionType.CONFIRMED

    def stop(self):
        """Stop traveling."""
        self.last_known_position = self.current_position()
        self.travel_to_position = self.last_known_position
        self.position_type = PositionType.CALCULATED
        self.travel_direction = TravelStatus.STOPPED

    def start_travel(self, travel_to_position):
        """Start traveling to position."""
        self.stop()
        self.travel_started_time = self.current_time()
        self.travel_to_position = travel_to_position
        self.position_type = PositionType.CALCULATED

        self.travel_direction = (
            TravelStatus.DIRECTION_UP
            if travel_to_position > self.last_known_position
            else TravelStatus.DIRECTION_DOWN
        )

    def start_travel_up(self):
        """Start traveling up."""
        self.start_travel(self.position_open)

    def start_travel_down(self):
        """Start traveling down."""
        self.start_travel(self.position_closed)

    def current_position(self):
        """Return current (calculated or known) position."""
        if self.position_type == PositionType.CALCULATED:
            return self._calculate_position()
        return self.last_known_position

    def is_traveling(self):
        """Return if cover is traveling."""
        return self.current_position() != self.travel_to_position

    def position_reached(self):
        """Return if cover has reached designated position."""
        if self.travel_direction == TravelStatus.DIRECTION_UP:
            return self.current_position() >= self.travel_to_position
        elif self.travel_direction == TravelStatus.DIRECTION_DOWN:
            return self.current_position() <= self.travel_to_position
        else:
            return self.current_position() == self.travel_to_position

    def is_open(self):
        """Return if cover is (fully) open."""
        return self.current_position() == self.position_open

    def is_closed(self):
        """Return if cover is (fully) closed."""
        return self.current_position() == self.position_closed

    def _calculate_position(self):
        """Return calculated position using multi-segment travel."""
        relative_position = self.travel_to_position - self.last_known_position

        def position_reached_or_exceeded(relative_position):
            """Return if designated position was reached."""
            if (
                relative_position >= 0
                and self.travel_direction == TravelStatus.DIRECTION_DOWN
            ):
                return True
            if (
                relative_position <= 0
                and self.travel_direction == TravelStatus.DIRECTION_UP
            ):
                return True
            return False

        if position_reached_or_exceeded(relative_position):
            return self.travel_to_position

        elapsed_time = self.current_time() - self.travel_started_time
        position = self._position_from_time(elapsed_time)

        # Check if target reached
        if self.travel_direction == TravelStatus.DIRECTION_UP:
            if position >= self.travel_to_position:
                return self.travel_to_position
        else:
            if position <= self.travel_to_position:
                return self.travel_to_position
        return int(position)

    def _position_from_time(self, elapsed_time):
        """Calculate position from elapsed time using segments."""
        segments = (
            self.segments_up
            if self.travel_direction == TravelStatus.DIRECTION_UP
            else self.segments_down
        )
        start_pos = self.last_known_position
        end_pos = self.travel_to_position

        # Calculate which segments we need to traverse
        traversed_segments = self._calculate_traversed_segments(
            segments, start_pos, end_pos
        )
        if not traversed_segments:
            return start_pos

        # Calculate position based on elapsed time through segments
        time_accumulated = 0
        current_pos = start_pos

        for seg_start, seg_end, seg_duration in traversed_segments:
            if elapsed_time <= time_accumulated + seg_duration:
                # We're in this segment
                progress = (elapsed_time - time_accumulated) / seg_duration
                position = seg_start + (seg_end - seg_start) * progress
                return position

            time_accumulated += seg_duration
            current_pos = seg_end

        return current_pos

    def _calculate_traversed_segments(self, segments, start_pos, end_pos):
        """Calculate which segments are traversed and their proportional durations.

        Works for both upward and downward travel using symmetric segment definitions.
        Segments are defined as [(position_end, duration), ...] from 0 to 100.
        """
        is_going_up = self.travel_direction == TravelStatus.DIRECTION_UP
        result = []

        # Build segment ranges
        segment_ranges = []
        prev_pos = 0

        for seg_end, seg_time in segments:
            segment_ranges.append((prev_pos, seg_end, seg_time))
            prev_pos = seg_end

        # For downward travel, process segments in reverse order
        ranges_to_process = segment_ranges if is_going_up else reversed(segment_ranges)

        for seg_start, seg_end, seg_time in ranges_to_process:
            if is_going_up:
                # Skip segments entirely below our starting position
                if seg_end <= start_pos:
                    continue

                # Calculate actual start and end within this segment
                actual_start = max(seg_start, start_pos)
                actual_end = min(seg_end, end_pos)

                # Check if we have a valid range
                if actual_end > actual_start:
                    seg_range = seg_end - seg_start
                    actual_range = actual_end - actual_start
                    proportional_time = seg_time * (actual_range / seg_range)
                    result.append((actual_start, actual_end, proportional_time))

                # Stop if we've reached the end position
                if seg_end >= end_pos:
                    break
            else:
                # Skip segments entirely above our starting position
                if seg_start >= start_pos:
                    continue

                # Calculate actual start and end within this segment
                actual_start = min(start_pos, seg_end)
                actual_end = max(end_pos, seg_start)

                # Check if we have a valid range
                if actual_start > actual_end:
                    seg_range = seg_end - seg_start
                    actual_range = actual_start - actual_end
                    proportional_time = seg_time * (actual_range / seg_range)
                    result.append((actual_start, actual_end, proportional_time))

                # Stop if we've reached the end position
                if seg_start <= end_pos:
                    break

        return result

    def current_time(self):
        """Get current time. May be modified from outside (for unit tests)."""
        # time_set_from_outside is  used within unit tests
        if self.time_set_from_outside is not None:
            return self.time_set_from_outside
        return time.time()

    def __eq__(self, other):
        """Equal operator."""
        return self.__dict__ == other.__dict__
