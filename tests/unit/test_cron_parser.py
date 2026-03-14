import pytest

from app.jobs.run_sync import _parse_cron


class TestParseCron:
    def test_every_15_minutes(self):
        result = _parse_cron("*/15 * * * *")
        assert result == {
            "minute": "*/15",
            "hour": "*",
            "day": "*",
            "month": "*",
            "day_of_week": "*",
        }

    def test_specific_time(self):
        result = _parse_cron("0 3 * * 1")
        assert result["minute"] == "0"
        assert result["hour"] == "3"
        assert result["day_of_week"] == "1"

    def test_invalid_too_few_fields(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("*/15 * *")

    def test_invalid_too_many_fields(self):
        with pytest.raises(ValueError, match="5-field"):
            _parse_cron("*/15 * * * * *")
