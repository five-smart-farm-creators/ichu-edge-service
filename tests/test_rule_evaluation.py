import unittest
import uuid
from datetime import UTC, datetime
from math import cos, pi, radians

from monitoring.domain.entities import MonitoringRuleSet, SubmittingDevice, TelemetryReading
from monitoring.domain.services import RuleEvaluator, distance_to_boundary_meters, point_in_polygon
from monitoring.domain.value_objects import (
    AlertSeverity,
    AlertType,
    Coordinate,
    DeviceAssignment,
    Geofence,
    GeoPosition,
    MonitoredMetric,
    Threshold,
)


DEVICE_ID = "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
LOT_ID = uuid.UUID("6a1f2c0e-0d3b-4c51-9a77-1b2c3d4e5f60")
EARTH_RADIUS_METERS = 6_371_000.0
CENTER_LATITUDE = -20.0
CENTER_LONGITUDE = -68.0
HALF_SIDE_DEGREES = 0.0005
MILKING_COW_TEMPERATURE = Threshold(
    uuid.uuid4(), "MilkingCow", MonitoredMetric.BODY_TEMPERATURE, 37.5, 39.5, AlertSeverity.WARNING
)
MILKING_COW_HEART_RATE = Threshold(
    uuid.uuid4(), "MilkingCow", MonitoredMetric.HEART_RATE, 48.0, 84.0, AlertSeverity.WARNING
)
MILKING_COW_HEART_RATE_CRITICAL = Threshold(
    uuid.uuid4(), "MilkingCow", MonitoredMetric.HEART_RATE, 40.0, 90.0, AlertSeverity.CRITICAL
)
MILKING_COW_METHANE_WARNING = Threshold(
    uuid.uuid4(), "MilkingCow", MonitoredMetric.METHANE_INDEX, 0.0, 60.0, AlertSeverity.WARNING
)
MILKING_COW_METHANE_CRITICAL = Threshold(
    uuid.uuid4(), "MilkingCow", MonitoredMetric.METHANE_INDEX, 0.0, 80.0, AlertSeverity.CRITICAL
)


def square_around(latitude, longitude, half_side):
    return (
        Coordinate(latitude - half_side, longitude - half_side),
        Coordinate(latitude - half_side, longitude + half_side),
        Coordinate(latitude + half_side, longitude + half_side),
        Coordinate(latitude + half_side, longitude - half_side),
    )


def longitude_degrees_for_meters(meters, latitude):
    return meters / (EARTH_RADIUS_METERS * cos(radians(latitude)) * pi / 180.0)


def reading(temperature=None, activity=None, position=None, heart_rate=None, methane_index=None):
    return TelemetryReading.record(
        SubmittingDevice(DEVICE_ID),
        "event-1",
        datetime(2026, 11, 7, 12, tzinfo=UTC),
        temperature,
        activity,
        None,
        position,
        heart_rate=heart_rate,
        methane_index=methane_index,
    )


def rules(thresholds=(), geofences=()):
    return MonitoringRuleSet(uuid.uuid4(), 3, tuple(thresholds), tuple(geofences))


class ThresholdEvaluationTestCase(unittest.TestCase):
    def setUp(self):
        self.evaluator = RuleEvaluator()
        self.milking_cow = DeviceAssignment(DEVICE_ID, None, "MilkingCow")
        self.rules = rules(thresholds=(MILKING_COW_TEMPERATURE,))

    def test_temperature_above_threshold_raises_a_temperature_violation(self):
        violations = self.evaluator.evaluate(reading(temperature=40.0), self.milking_cow, self.rules)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, AlertType.TEMPERATURE)
        self.assertEqual(violations[0].severity, AlertSeverity.WARNING)
        self.assertEqual(violations[0].threshold_min_value, 37.5)
        self.assertEqual(violations[0].threshold_max_value, 39.5)

    def test_temperature_inside_threshold_raises_nothing(self):
        self.assertEqual(self.evaluator.evaluate(reading(temperature=38.5), self.milking_cow, self.rules), [])

    def test_life_stage_mismatch_is_skipped(self):
        heifer = DeviceAssignment(DEVICE_ID, None, "Heifer")
        self.assertEqual(self.evaluator.evaluate(reading(temperature=41.0), heifer, self.rules), [])

    def test_life_stage_comparison_ignores_case(self):
        lower_case = DeviceAssignment(DEVICE_ID, None, "milkingcow")
        self.assertEqual(len(self.evaluator.evaluate(reading(temperature=41.0), lower_case, self.rules)), 1)

    def test_device_without_assignment_is_not_evaluated(self):
        self.assertEqual(self.evaluator.evaluate(reading(temperature=41.0), None, self.rules), [])

    def test_activity_threshold_applies_to_the_activity_index(self):
        activity_threshold = Threshold(
            uuid.uuid4(), "MilkingCow", MonitoredMetric.ACTIVITY_INDEX, 10.0, 80.0, AlertSeverity.INFORMATIVE
        )
        violations = self.evaluator.evaluate(
            reading(activity=95.0), self.milking_cow, rules(thresholds=(activity_threshold,))
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, AlertType.ACTIVITY)


class HeartRateEvaluationTestCase(unittest.TestCase):
    def setUp(self):
        self.evaluator = RuleEvaluator()
        self.milking_cow = DeviceAssignment(DEVICE_ID, None, "MilkingCow")
        self.rules = rules(thresholds=(MILKING_COW_HEART_RATE,))

    def test_heart_rate_above_threshold_raises_a_heart_rate_violation(self):
        violations = self.evaluator.evaluate(reading(heart_rate=120.0), self.milking_cow, self.rules)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, AlertType.HEART_RATE)
        self.assertEqual(violations[0].severity, AlertSeverity.WARNING)
        self.assertEqual(violations[0].threshold_min_value, 48.0)
        self.assertEqual(violations[0].threshold_max_value, 84.0)

    def test_heart_rate_inside_threshold_raises_nothing(self):
        self.assertEqual(self.evaluator.evaluate(reading(heart_rate=70.0), self.milking_cow, self.rules), [])

    def test_reading_without_heart_rate_raises_no_heart_rate_violation(self):
        self.assertEqual(self.evaluator.evaluate(reading(temperature=38.5), self.milking_cow, self.rules), [])


class TwoLevelEvaluationTestCase(unittest.TestCase):
    def setUp(self):
        self.evaluator = RuleEvaluator()
        self.milking_cow = DeviceAssignment(DEVICE_ID, None, "MilkingCow")

    def test_reading_breaking_both_levels_raises_one_critical_violation(self):
        for ordered in (
            (MILKING_COW_HEART_RATE, MILKING_COW_HEART_RATE_CRITICAL),
            (MILKING_COW_HEART_RATE_CRITICAL, MILKING_COW_HEART_RATE),
        ):
            with self.subTest(first_listed=ordered[0].severity):
                violations = self.evaluator.evaluate(
                    reading(heart_rate=100.0), self.milking_cow, rules(thresholds=ordered)
                )

                self.assertEqual(len(violations), 1)
                self.assertEqual(violations[0].severity, AlertSeverity.CRITICAL)
                self.assertEqual(
                    (violations[0].threshold_min_value, violations[0].threshold_max_value), (40.0, 90.0)
                )

    def test_reading_breaking_only_the_warning_raises_a_warning_violation(self):
        violations = self.evaluator.evaluate(
            reading(heart_rate=85.0),
            self.milking_cow,
            rules(thresholds=(MILKING_COW_HEART_RATE, MILKING_COW_HEART_RATE_CRITICAL)),
        )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].severity, AlertSeverity.WARNING)
        self.assertEqual((violations[0].threshold_min_value, violations[0].threshold_max_value), (48.0, 84.0))


class MethaneEvaluationTestCase(unittest.TestCase):
    def setUp(self):
        self.evaluator = RuleEvaluator()
        self.milking_cow = DeviceAssignment(DEVICE_ID, None, "MilkingCow")
        self.rules = rules(thresholds=(MILKING_COW_METHANE_WARNING, MILKING_COW_METHANE_CRITICAL))

    def test_methane_index_above_the_warning_maximum_raises_a_methane_warning(self):
        violations = self.evaluator.evaluate(reading(methane_index=70.0), self.milking_cow, self.rules)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, AlertType.METHANE)
        self.assertEqual(violations[0].severity, AlertSeverity.WARNING)
        self.assertEqual((violations[0].threshold_min_value, violations[0].threshold_max_value), (0.0, 60.0))

    def test_methane_index_above_the_critical_maximum_raises_a_methane_critical(self):
        violations = self.evaluator.evaluate(reading(methane_index=95.0), self.milking_cow, self.rules)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].severity, AlertSeverity.CRITICAL)
        self.assertEqual(violations[0].threshold_max_value, 80.0)

    def test_methane_index_inside_the_rules_raises_nothing(self):
        self.assertEqual(self.evaluator.evaluate(reading(methane_index=30.0), self.milking_cow, self.rules), [])

    def test_failed_methane_sensor_is_not_evaluated(self):
        failed = TelemetryReading.record(
            SubmittingDevice(DEVICE_ID),
            "event-2",
            None,
            None,
            None,
            None,
            None,
            {"methane_index": "Gas sensor warming up"},
        )

        self.assertEqual(self.evaluator.evaluate(failed, self.milking_cow, self.rules), [])


class GeofenceEvaluationTestCase(unittest.TestCase):
    def setUp(self):
        self.evaluator = RuleEvaluator()
        self.assignment = DeviceAssignment(DEVICE_ID, LOT_ID, None)
        self.geofence = Geofence(
            uuid.uuid4(),
            LOT_ID,
            "North pasture",
            50,
            AlertSeverity.CRITICAL,
            square_around(CENTER_LATITUDE, CENTER_LONGITUDE, HALF_SIDE_DEGREES),
        )
        self.rules = rules(geofences=(self.geofence,))

    def test_point_inside_square_polygon_is_inside(self):
        self.assertTrue(point_in_polygon(Coordinate(CENTER_LATITUDE, CENTER_LONGITUDE), self.geofence.vertices))
        self.assertFalse(
            point_in_polygon(Coordinate(CENTER_LATITUDE + 0.01, CENTER_LONGITUDE), self.geofence.vertices)
        )

    def test_center_of_square_is_half_a_side_from_the_boundary(self):
        distance = distance_to_boundary_meters(
            Coordinate(CENTER_LATITUDE, CENTER_LONGITUDE), self.geofence.vertices
        )
        self.assertAlmostEqual(distance, 52.2, delta=0.5)

    def test_point_100_meters_outside_with_tolerance_50_violates(self):
        longitude = CENTER_LONGITUDE + HALF_SIDE_DEGREES + longitude_degrees_for_meters(100.0, CENTER_LATITUDE)
        violations = self.evaluator.evaluate(
            reading(position=GeoPosition(CENTER_LATITUDE, longitude, 5.0)), self.assignment, self.rules
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, AlertType.GEOFENCE)
        self.assertEqual(violations[0].geofence_id, self.geofence.geofence_id)
        self.assertEqual(violations[0].severity, AlertSeverity.CRITICAL)
        self.assertAlmostEqual(violations[0].distance_to_boundary_meters, 100.0, delta=2.0)

    def test_point_within_tolerance_does_not_violate(self):
        longitude = CENTER_LONGITUDE + HALF_SIDE_DEGREES + longitude_degrees_for_meters(30.0, CENTER_LATITUDE)
        self.assertEqual(
            self.evaluator.evaluate(
                reading(position=GeoPosition(CENTER_LATITUDE, longitude)), self.assignment, self.rules
            ),
            [],
        )

    def test_geofence_of_another_lot_is_skipped(self):
        other_lot = DeviceAssignment(DEVICE_ID, uuid.uuid4(), None)
        longitude = CENTER_LONGITUDE + HALF_SIDE_DEGREES + longitude_degrees_for_meters(100.0, CENTER_LATITUDE)
        self.assertEqual(
            self.evaluator.evaluate(
                reading(position=GeoPosition(CENTER_LATITUDE, longitude)), other_lot, self.rules
            ),
            [],
        )

    def test_violations_are_ordered_by_severity(self):
        threshold = Threshold(
            uuid.uuid4(), "MilkingCow", MonitoredMetric.BODY_TEMPERATURE, 37.5, 39.5, AlertSeverity.INFORMATIVE
        )
        longitude = CENTER_LONGITUDE + HALF_SIDE_DEGREES + longitude_degrees_for_meters(100.0, CENTER_LATITUDE)
        violations = self.evaluator.evaluate(
            reading(temperature=41.0, position=GeoPosition(CENTER_LATITUDE, longitude)),
            DeviceAssignment(DEVICE_ID, LOT_ID, "MilkingCow"),
            rules(thresholds=(threshold,), geofences=(self.geofence,)),
        )
        self.assertEqual([violation.severity for violation in violations], [AlertSeverity.CRITICAL, AlertSeverity.INFORMATIVE])


class GeofenceAccuracyTestCase(unittest.TestCase):
    def setUp(self):
        self.evaluator = RuleEvaluator()
        self.assignment = DeviceAssignment(DEVICE_ID, LOT_ID, None)
        self.geofence = Geofence(
            uuid.uuid4(),
            LOT_ID,
            "South pasture",
            25,
            AlertSeverity.CRITICAL,
            square_around(CENTER_LATITUDE, CENTER_LONGITUDE, HALF_SIDE_DEGREES),
        )
        self.rules = rules(geofences=(self.geofence,))

    def evaluate_outside(self, meters_outside, accuracy_meters):
        longitude = CENTER_LONGITUDE + HALF_SIDE_DEGREES + longitude_degrees_for_meters(
            meters_outside, CENTER_LATITUDE
        )
        position = GeoPosition(CENTER_LATITUDE, longitude, accuracy_meters)
        return self.evaluator.evaluate(reading(position=position), self.assignment, self.rules)

    def test_position_within_tolerance_with_zero_accuracy_does_not_violate(self):
        self.assertEqual(self.evaluate_outside(20.0, 0.0), [])

    def test_accuracy_widens_the_tolerance_of_a_position_outside_it(self):
        self.assertEqual(self.evaluate_outside(40.0, 30.0), [])

    def test_position_beyond_tolerance_plus_accuracy_violates(self):
        violations = self.evaluate_outside(40.0, 5.0)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, AlertType.GEOFENCE)
        self.assertAlmostEqual(violations[0].distance_to_boundary_meters, 40.0, delta=1.0)

    def test_missing_accuracy_counts_as_zero(self):
        self.assertEqual(len(self.evaluate_outside(40.0, None)), 1)


if __name__ == "__main__":
    unittest.main()
