import math
import unittest
import uuid
from datetime import UTC, datetime, timedelta

from monitoring.domain.entities import MonitoringAlert, MonitoringRuleSet, SubmittingDevice, TelemetryReading
from monitoring.domain.value_objects import (
    ActivityIndex,
    AlertSeverity,
    AlertType,
    BatteryLevel,
    BodyTemperature,
    Coordinate,
    DeviceAssignment,
    Geofence,
    GeoPosition,
    HeartRate,
    InvalidMeasurementReasons,
    MethaneIndex,
    MonitoredMetric,
    RecordedAt,
    SyncStatus,
    TelemetryEventId,
    Threshold,
)


DEVICE_ID = "9d4a5f3b-3a6e-4f84-8daa-4e5f6a7b8c93"
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


class MeasurementValueObjectTestCase(unittest.TestCase):
    def test_measurements_accept_range_boundaries(self):
        self.assertEqual(BodyTemperature(30).celsius, 30.0)
        self.assertEqual(BodyTemperature(45.0).celsius, 45.0)
        self.assertEqual(ActivityIndex(0).value, 0.0)
        self.assertEqual(ActivityIndex(100).value, 100.0)
        self.assertEqual(BatteryLevel(0).percent, 0.0)
        self.assertEqual(BatteryLevel(100).percent, 100.0)
        self.assertEqual(HeartRate(30).beats_per_minute, 30.0)
        self.assertEqual(HeartRate(200).beats_per_minute, 200.0)
        self.assertEqual(MethaneIndex(0).value, 0.0)
        self.assertEqual(MethaneIndex(100).value, 100.0)

    def test_measurements_reject_out_of_range_non_finite_and_non_numeric_values(self):
        cases = (
            (BodyTemperature, 29.9),
            (BodyTemperature, 45.1),
            (BodyTemperature, math.nan),
            (BodyTemperature, math.inf),
            (BodyTemperature, True),
            (BodyTemperature, "38.5"),
            (ActivityIndex, -0.1),
            (ActivityIndex, 100.1),
            (BatteryLevel, -1),
            (BatteryLevel, 101),
            (HeartRate, 29.9),
            (HeartRate, 200.1),
            (MethaneIndex, -0.1),
            (MethaneIndex, 100.1),
        )
        for factory, value in cases:
            with self.subTest(factory=factory.__name__, value=value):
                with self.assertRaises(ValueError):
                    factory(value)


class PositionValueObjectTestCase(unittest.TestCase):
    def test_coordinate_and_position_enforce_geographic_ranges(self):
        self.assertEqual(Coordinate(-90, 180).latitude, -90.0)
        for latitude, longitude in ((90.1, 0), (0, -180.5)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(ValueError):
                    Coordinate(latitude, longitude)
                with self.assertRaises(ValueError):
                    GeoPosition(latitude, longitude)

    def test_position_rejects_negative_accuracy(self):
        self.assertEqual(GeoPosition(-16.5, -68.1, 4.0).accuracy_meters, 4.0)
        with self.assertRaises(ValueError):
            GeoPosition(-16.5, -68.1, -1.0)


class InvalidMeasurementReasonsTestCase(unittest.TestCase):
    def test_reasons_are_stripped_and_looked_up_by_field(self):
        reasons = InvalidMeasurementReasons({"body_temperature": "  Sensor did not respond  "})
        self.assertEqual(reasons.reason_for("body_temperature"), "Sensor did not respond")
        self.assertIsNone(reasons.reason_for("battery_level"))
        self.assertEqual(reasons.fields(), frozenset({"body_temperature"}))
        self.assertEqual(reasons.as_dict(), {"body_temperature": "Sensor did not respond"})

    def test_empty_reasons_are_falsy(self):
        self.assertFalse(InvalidMeasurementReasons())
        self.assertFalse(InvalidMeasurementReasons(None))
        self.assertTrue(InvalidMeasurementReasons({"position": "GPS module offline"}))

    def test_rejects_unknown_fields_and_blank_non_text_or_overlong_reasons(self):
        cases = (
            {"blood_pressure": "Sensor did not respond"},
            {"body_temperature": 42},
            {"body_temperature": "   "},
            {"body_temperature": "x" * 201},
        )
        for reasons in cases:
            with self.subTest(reasons=reasons):
                with self.assertRaises(ValueError):
                    InvalidMeasurementReasons(reasons)

    def test_reason_of_exactly_200_characters_is_accepted(self):
        reason = "x" * 200
        self.assertEqual(InvalidMeasurementReasons({"position": reason}).reason_for("position"), reason)

    def test_pulse_and_methane_index_accept_reasons(self):
        reasons = InvalidMeasurementReasons(
            {"heart_rate": "Pulse sensor without contact", "methane_index": "Gas sensor warming up"}
        )
        self.assertEqual(reasons.fields(), frozenset({"heart_rate", "methane_index"}))

    def test_equality_and_order_ignore_insertion_order(self):
        first = InvalidMeasurementReasons(
            {"position": "GPS module offline", "body_temperature": "Sensor did not respond"}
        )
        second = InvalidMeasurementReasons(
            {"body_temperature": "Sensor did not respond", "position": "GPS module offline"}
        )
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))
        self.assertEqual(list(first.as_dict()), ["body_temperature", "position"])


class TimeAndIdentityValueObjectTestCase(unittest.TestCase):
    def test_recorded_at_normalizes_offset_to_utc(self):
        recorded_at = RecordedAt(datetime.fromisoformat("2026-11-07T10:00:00-05:00"))
        self.assertEqual(recorded_at.value.utcoffset(), timedelta(0))
        self.assertEqual(recorded_at.value.hour, 15)

    def test_recorded_at_rejects_naive_time(self):
        with self.assertRaises(ValueError):
            RecordedAt(datetime(2026, 11, 7, 10))

    def test_event_id_rejects_blank_and_overlong_values(self):
        for event_id in ("", "   ", "x" * 129):
            with self.subTest(event_id=event_id):
                with self.assertRaises(ValueError):
                    TelemetryEventId(event_id)

    def test_submitting_device_requires_a_canonical_uuid(self):
        self.assertEqual(SubmittingDevice(DEVICE_ID.upper()).device_id, DEVICE_ID)
        with self.assertRaises(ValueError):
            SubmittingDevice("band-42")

    def test_device_assignment_requires_a_uuid_device(self):
        with self.assertRaises(ValueError):
            DeviceAssignment("band-42", None, "Heifer")


class ThresholdAndGeofenceTestCase(unittest.TestCase):
    def test_threshold_requires_minimum_below_maximum(self):
        with self.assertRaises(ValueError):
            Threshold(
                uuid.uuid4(), "MilkingCow", MonitoredMetric.BODY_TEMPERATURE, 39.5, 37.5, AlertSeverity.WARNING
            )

    def test_threshold_requires_typed_metric_and_severity(self):
        with self.assertRaises(TypeError):
            Threshold(uuid.uuid4(), "MilkingCow", "BodyTemperature", 37.5, 39.5, AlertSeverity.WARNING)
        with self.assertRaises(TypeError):
            Threshold(uuid.uuid4(), "MilkingCow", MonitoredMetric.BODY_TEMPERATURE, 37.5, 39.5, "Warning")

    def test_geofence_requires_three_vertices_and_non_negative_tolerance(self):
        square = (Coordinate(0, 0), Coordinate(0, 1), Coordinate(1, 1))
        fence = Geofence(uuid.uuid4(), uuid.uuid4(), "North pasture", 50, AlertSeverity.WARNING, square)
        self.assertEqual(fence.vertices, square)
        with self.assertRaises(ValueError):
            Geofence(uuid.uuid4(), uuid.uuid4(), "Open", 50, AlertSeverity.WARNING, square[:2])
        with self.assertRaises(ValueError):
            Geofence(uuid.uuid4(), uuid.uuid4(), "Open", -1, AlertSeverity.WARNING, square)


class TelemetryReadingTestCase(unittest.TestCase):
    def setUp(self):
        self.device = SubmittingDevice(DEVICE_ID)

    def test_reading_requires_at_least_one_measurement(self):
        with self.assertRaises(ValueError):
            TelemetryReading.record(self.device, "event-1", None, None, None, None, None)

    def test_reading_marks_whether_the_device_supplied_the_time(self):
        supplied = TelemetryReading.record(self.device, "event-2", NOW, 38.5, None, None, None)
        generated = TelemetryReading.record(self.device, "event-3", None, 38.5, None, None, None)
        self.assertTrue(supplied.timestamp_supplied)
        self.assertFalse(generated.timestamp_supplied)
        self.assertEqual(supplied.sync_status, SyncStatus.PENDING)

    def test_sync_transitions_keep_the_rejection_reason_consistent(self):
        reading = TelemetryReading.record(self.device, "event-4", None, 38.5, None, None, None)
        synced = reading.mark_synced()
        rejected = reading.mark_rejected("Unknown device")
        self.assertEqual(synced.sync_status, SyncStatus.SYNCED)
        self.assertEqual(rejected.rejection_reason, "Unknown device")
        self.assertIsNone(synced.rejection_reason)
        with self.assertRaises(ValueError):
            reading.mark_rejected("   ")

    def test_same_telemetry_compares_measurements_and_supplied_time(self):
        position = GeoPosition(-16.5, -68.1, 3.0)
        original = TelemetryReading.record(self.device, "event-5", NOW, 38.5, None, None, position)
        retry = TelemetryReading.record(self.device, "event-5", NOW, 38.5, None, None, position)
        changed_temperature = TelemetryReading.record(self.device, "event-5", NOW, 38.6, None, None, position)
        later = TelemetryReading.record(
            self.device, "event-5", NOW + timedelta(minutes=1), 38.5, None, None, position
        )
        generated = TelemetryReading.record(self.device, "event-5", None, 38.5, None, None, position)
        self.assertTrue(original.same_telemetry_as(retry))
        self.assertFalse(original.same_telemetry_as(changed_temperature))
        self.assertFalse(original.same_telemetry_as(later))
        self.assertFalse(original.same_telemetry_as(generated))

    def test_generated_time_is_ignored_when_comparing_retries(self):
        first = TelemetryReading.record(self.device, "event-6", None, 38.5, None, None, None)
        second = TelemetryReading.record(self.device, "event-6", None, 38.5, None, None, None)
        self.assertTrue(first.same_telemetry_as(second))

    def test_field_cannot_carry_both_a_value_and_an_invalid_reason(self):
        with self.assertRaises(ValueError):
            TelemetryReading.record(
                self.device,
                "event-7",
                None,
                38.5,
                None,
                None,
                None,
                {"body_temperature": "Sensor did not respond"},
            )

    def test_reading_with_only_invalid_reasons_is_valid(self):
        reading = TelemetryReading.record(
            self.device,
            "event-8",
            None,
            None,
            None,
            None,
            None,
            {"body_temperature": "Sensor did not respond"},
        )
        self.assertIsNone(reading.body_temperature)
        self.assertEqual(reading.invalid_reasons.reason_for("body_temperature"), "Sensor did not respond")

    def test_same_telemetry_compares_invalid_reasons(self):
        original = TelemetryReading.record(
            self.device, "event-9", None, None, 40.0, None, None, {"battery_level": "Battery sensor offline"}
        )
        retry = TelemetryReading.record(
            self.device, "event-9", None, None, 40.0, None, None, {"battery_level": "Battery sensor offline"}
        )
        other_reason = TelemetryReading.record(
            self.device, "event-9", None, None, 40.0, None, None, {"battery_level": "Charger unplugged"}
        )
        self.assertTrue(original.same_telemetry_as(retry))
        self.assertFalse(original.same_telemetry_as(other_reason))

    def test_pulse_and_methane_index_are_optional_value_objects(self):
        reading = TelemetryReading.record(
            self.device, "event-10", None, None, None, None, None, heart_rate=72.0, methane_index=18.5
        )
        bare = TelemetryReading.record(self.device, "event-11", None, 38.5, None, None, None)

        self.assertEqual(reading.heart_rate, HeartRate(72.0))
        self.assertEqual(reading.methane_index, MethaneIndex(18.5))
        self.assertIsNone(bare.heart_rate)
        self.assertIsNone(bare.methane_index)

    def test_reading_with_only_a_pulse_or_a_methane_index_is_valid(self):
        pulse_only = TelemetryReading.record(
            self.device, "event-12", None, None, None, None, None, heart_rate=72.0
        )
        methane_only = TelemetryReading.record(
            self.device, "event-13", None, None, None, None, None, methane_index=18.5
        )

        self.assertEqual(pulse_only.heart_rate, HeartRate(72.0))
        self.assertEqual(methane_only.methane_index, MethaneIndex(18.5))

    def test_pulse_cannot_carry_both_a_value_and_an_invalid_reason(self):
        with self.assertRaises(ValueError):
            TelemetryReading.record(
                self.device,
                "event-14",
                None,
                None,
                None,
                None,
                None,
                {"heart_rate": "Pulse sensor without contact"},
                heart_rate=72.0,
            )

    def test_same_telemetry_compares_pulse_and_methane_index(self):
        original = TelemetryReading.record(
            self.device, "event-15", NOW, None, None, None, None, heart_rate=72.0, methane_index=18.5
        )
        retry = TelemetryReading.record(
            self.device, "event-15", NOW, None, None, None, None, heart_rate=72.0, methane_index=18.5
        )
        other_pulse = TelemetryReading.record(
            self.device, "event-15", NOW, None, None, None, None, heart_rate=80.0, methane_index=18.5
        )
        other_methane = TelemetryReading.record(
            self.device, "event-15", NOW, None, None, None, None, heart_rate=72.0, methane_index=20.0
        )
        self.assertTrue(original.same_telemetry_as(retry))
        self.assertFalse(original.same_telemetry_as(other_pulse))
        self.assertFalse(original.same_telemetry_as(other_methane))


class MonitoringAlertTestCase(unittest.TestCase):
    def setUp(self):
        self.reading_id = uuid.uuid4()

    def test_threshold_alert_requires_threshold_bounds(self):
        alert = MonitoringAlert.raise_for(
            DEVICE_ID, AlertType.TEMPERATURE, AlertSeverity.WARNING, (self.reading_id,), 3, NOW, 37.5, 39.5
        )
        self.assertEqual(alert.sync_status, SyncStatus.PENDING)
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID, AlertType.TEMPERATURE, AlertSeverity.WARNING, (self.reading_id,), 3, NOW
            )
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID,
                AlertType.TEMPERATURE,
                AlertSeverity.WARNING,
                (self.reading_id,),
                3,
                NOW,
                37.5,
                39.5,
                geofence_id=uuid.uuid4(),
                distance_to_boundary_meters=10.0,
            )

    def test_heart_rate_alert_is_a_threshold_alert(self):
        alert = MonitoringAlert.raise_for(
            DEVICE_ID, AlertType.HEART_RATE, AlertSeverity.WARNING, (self.reading_id,), 3, NOW, 48.0, 84.0
        )
        self.assertEqual(alert.threshold_max_value, 84.0)
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID, AlertType.HEART_RATE, AlertSeverity.WARNING, (self.reading_id,), 3, NOW
            )

    def test_methane_alert_is_a_threshold_alert(self):
        alert = MonitoringAlert.raise_for(
            DEVICE_ID, AlertType.METHANE, AlertSeverity.CRITICAL, (self.reading_id,), 3, NOW, 0.0, 80.0
        )
        self.assertEqual(alert.threshold_max_value, 80.0)
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID, AlertType.METHANE, AlertSeverity.CRITICAL, (self.reading_id,), 3, NOW
            )

    def test_geofence_alert_requires_geofence_and_distance_only(self):
        alert = MonitoringAlert.raise_for(
            DEVICE_ID,
            AlertType.GEOFENCE,
            AlertSeverity.CRITICAL,
            (self.reading_id,),
            3,
            NOW,
            geofence_id=uuid.uuid4(),
            distance_to_boundary_meters=100.0,
        )
        self.assertEqual(alert.type, AlertType.GEOFENCE)
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID,
                AlertType.GEOFENCE,
                AlertSeverity.CRITICAL,
                (self.reading_id,),
                3,
                NOW,
                distance_to_boundary_meters=100.0,
            )
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID,
                AlertType.GEOFENCE,
                AlertSeverity.CRITICAL,
                (self.reading_id,),
                3,
                NOW,
                37.5,
                39.5,
                geofence_id=uuid.uuid4(),
                distance_to_boundary_meters=100.0,
            )

    def test_alert_requires_a_reading_and_a_non_negative_revision(self):
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(DEVICE_ID, AlertType.TEMPERATURE, AlertSeverity.WARNING, (), 3, NOW, 37.5, 39.5)
        with self.assertRaises(ValueError):
            MonitoringAlert.raise_for(
                DEVICE_ID, AlertType.TEMPERATURE, AlertSeverity.WARNING, (self.reading_id,), -1, NOW, 37.5, 39.5
            )


class MonitoringRuleSetTestCase(unittest.TestCase):
    def test_empty_rule_set_has_revision_zero(self):
        rules = MonitoringRuleSet.empty()
        self.assertEqual(rules.revision, 0)
        self.assertEqual(rules.thresholds, ())
        self.assertEqual(rules.geofences, ())

    def test_rule_set_rejects_negative_revision(self):
        with self.assertRaises(ValueError):
            MonitoringRuleSet(None, -1, (), ())


if __name__ == "__main__":
    unittest.main()
