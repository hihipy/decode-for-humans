"""
test_python.py

A comprehensive data pipeline for processing customer survey results.

Reads raw CSV exports from a survey platform, cleans and validates each
response, scores them against a weighted rubric, and writes a summary
report as both CSV and a printed table.

Dependencies:
    pip install rich
"""

from __future__ import annotations

import csv
import json
import logging
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Generator, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCORE_WEIGHTS: dict[str, float] = {
    "satisfaction": 0.40,
    "ease_of_use":  0.30,
    "likelihood_recommend": 0.30,
}

MAX_SCORE: float = 10.0
PASSING_THRESHOLD: float = 6.5
MISSING_VALUE_PLACEHOLDER: str = "N/A"
DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SurveyStatus(Enum):
    """Lifecycle states a survey response can be in."""

    PENDING   = auto()
    VALID     = auto()
    INVALID   = auto()
    DUPLICATE = auto()


class ReportFormat(Enum):
    """Output formats supported by the report writer."""

    CSV  = "csv"
    JSON = "json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RawResponse:
    """A single raw row read directly from the CSV export.

    Attributes:
        respondent_id: Unique identifier assigned by the survey platform.
        submitted_at: ISO 8601 timestamp string from the export.
        answers: Raw string values keyed by question slug.
    """

    respondent_id: str
    submitted_at: str
    answers: dict[str, str] = field(default_factory=dict)


@dataclass
class ScoredResponse:
    """A validated and scored survey response.

    Attributes:
        respondent_id: Carried forward from RawResponse.
        submitted_at: Parsed datetime object.
        scores: Numeric score per question (0.0 – 10.0).
        weighted_total: Final composite score after applying SCORE_WEIGHTS.
        status: Validation outcome for this response.
    """

    respondent_id: str
    submitted_at: datetime
    scores: dict[str, float] = field(default_factory=dict)
    weighted_total: float = 0.0
    status: SurveyStatus = SurveyStatus.PENDING

    @property
    def passes(self) -> bool:
        """Return True if this response meets the passing threshold."""
        return (
            self.status == SurveyStatus.VALID
            and self.weighted_total >= PASSING_THRESHOLD
        )


# ---------------------------------------------------------------------------
# Abstract base — validator
# ---------------------------------------------------------------------------

class BaseValidator(ABC):
    """Abstract base class for all response validators."""

    @abstractmethod
    def validate(self, response: RawResponse) -> tuple[bool, str]:
        """Check whether a raw response is valid.

        Args:
            response: The raw response to validate.

        Returns:
            A (is_valid, reason) tuple. If is_valid is False, reason
            explains why the response was rejected.
        """


class CompletenessValidator(BaseValidator):
    """Reject responses that are missing required question answers."""

    def __init__(self, required_keys: list[str]) -> None:
        """Initialise with the set of question slugs that must be present.

        Args:
            required_keys: List of question slug strings that must exist
                           and be non-empty in every valid response.
        """
        self._required_keys = required_keys

    def validate(self, response: RawResponse) -> tuple[bool, str]:
        """Check all required keys are present and non-empty.

        Args:
            response: The raw response to inspect.

        Returns:
            (True, "") if complete, or (False, explanation) if not.
        """
        for key in self._required_keys:
            value = response.answers.get(key, "").strip()
            if not value or value == MISSING_VALUE_PLACEHOLDER:
                return False, f"Missing required answer: {key}"
        return True, ""


class RangeValidator(BaseValidator):
    """Reject responses where any numeric answer is out of range."""

    def __init__(self, low: float, high: float) -> None:
        """Initialise with the acceptable numeric range.

        Args:
            low: Minimum acceptable numeric value (inclusive).
            high: Maximum acceptable numeric value (inclusive).
        """
        self._low = low
        self._high = high

    def validate(self, response: RawResponse) -> tuple[bool, str]:
        """Check all numeric answers fall within the declared range.

        Args:
            response: The raw response to inspect.

        Returns:
            (True, "") if all in range, or (False, explanation) if not.
        """
        for key, raw_value in response.answers.items():
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if not self._low <= value <= self._high:
                return (
                    False,
                    f"Answer out of range for {key}: {value} "
                    f"(expected {self._low}–{self._high})",
                )
        return True, ""


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ResponseScorer:
    """Converts a validated RawResponse into a ScoredResponse.

    Attributes:
        weights: Question-slug to weight mapping used for the composite score.
    """

    def __init__(self, weights: dict[str, float]) -> None:
        """Initialise with a scoring weights dict.

        Args:
            weights: Maps question slugs to their fractional weight.
                     Values should sum to 1.0.
        """
        self.weights = weights

    def score(self, response: RawResponse) -> ScoredResponse:
        """Parse, score, and return a ScoredResponse.

        Args:
            response: A raw response that has already passed validation.

        Returns:
            A ScoredResponse with individual and weighted scores filled in.
        """
        try:
            submitted_at = datetime.strptime(
                response.submitted_at, DATE_FORMAT
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            submitted_at = datetime.now(timezone.utc)

        scores: dict[str, float] = {}
        for key in self.weights:
            raw = response.answers.get(key, "0")
            try:
                scores[key] = min(max(float(raw), 0.0), MAX_SCORE)
            except ValueError:
                scores[key] = 0.0

        weighted_total = sum(
            scores[key] * weight
            for key, weight in self.weights.items()
            if key in scores
        )

        return ScoredResponse(
            respondent_id=response.respondent_id,
            submitted_at=submitted_at,
            scores=scores,
            weighted_total=round(weighted_total, 2),
            status=SurveyStatus.VALID,
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class SurveyPipeline:
    """Orchestrates the full read → validate → score → report pipeline.

    Attributes:
        input_path: Path to the CSV file containing raw survey exports.
        validators: Ordered list of validators applied to each response.
        scorer: Scorer instance used to produce weighted totals.
    """

    def __init__(
        self,
        input_path: Path,
        validators: list[BaseValidator],
        scorer: ResponseScorer,
    ) -> None:
        """Initialise the pipeline with its data source and processing steps.

        Args:
            input_path: CSV file to read raw responses from.
            validators: Validators applied in order; first failure rejects.
            scorer: Converts valid raw responses into scored ones.
        """
        self.input_path = input_path
        self.validators = validators
        self.scorer = scorer

    @contextmanager
    def _open_csv(self) -> Generator:
        """Context manager that opens the CSV file and yields a DictReader.

        Yields:
            A csv.DictReader positioned at the first data row.

        Raises:
            FileNotFoundError: If input_path does not exist.
        """
        with self.input_path.open(encoding="utf-8", newline="") as f:
            yield csv.DictReader(f)

    def _iter_raw(self) -> Iterator[RawResponse]:
        """Parse each CSV row into a RawResponse object.

        Yields:
            One RawResponse per non-header row in the CSV.
        """
        with self._open_csv() as reader:
            for row in reader:
                yield RawResponse(
                    respondent_id=row.get("id", ""),
                    submitted_at=row.get("submitted_at", ""),
                    answers={
                        k: v for k, v in row.items()
                        if k not in {"id", "submitted_at"}
                    },
                )

    def run(self) -> list[ScoredResponse]:
        """Execute the full pipeline and return scored responses.

        Returns:
            List of ScoredResponse objects, including invalid ones whose
            status will be SurveyStatus.INVALID.
        """
        results: list[ScoredResponse] = []
        seen_ids: set[str] = set()

        for raw in self._iter_raw():
            if raw.respondent_id in seen_ids:
                logger.warning("Duplicate respondent: %s", raw.respondent_id)
                dummy = ScoredResponse(
                    respondent_id=raw.respondent_id,
                    submitted_at=datetime.now(timezone.utc),
                    status=SurveyStatus.DUPLICATE,
                )
                results.append(dummy)
                continue

            seen_ids.add(raw.respondent_id)
            rejection_reason = ""

            for validator in self.validators:
                is_valid, reason = validator.validate(raw)
                if not is_valid:
                    rejection_reason = reason
                    break

            if rejection_reason:
                logger.info("Rejected %s — %s", raw.respondent_id, rejection_reason)
                dummy = ScoredResponse(
                    respondent_id=raw.respondent_id,
                    submitted_at=datetime.now(timezone.utc),
                    status=SurveyStatus.INVALID,
                )
                results.append(dummy)
                continue

            scored = self.scorer.score(raw)
            results.append(scored)

        return results


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_report(
    responses: list[ScoredResponse],
    output_path: Path,
    fmt: ReportFormat = ReportFormat.CSV,
) -> None:
    """Write scored responses to a file in the specified format.

    Args:
        responses: List of ScoredResponse objects to export.
        output_path: Destination file path.
        fmt: Output format (CSV or JSON).
    """
    if fmt == ReportFormat.CSV:
        _write_csv(responses, output_path)
    elif fmt == ReportFormat.JSON:
        _write_json(responses, output_path)


def _write_csv(responses: list[ScoredResponse], output_path: Path) -> None:
    """Write responses as a CSV file.

    Args:
        responses: Scored responses to serialise.
        output_path: Destination CSV file path.
    """
    fieldnames = [
        "respondent_id", "submitted_at", "weighted_total",
        "status", "passes",
        *SCORE_WEIGHTS.keys(),
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in responses:
            row = {
                "respondent_id":  r.respondent_id,
                "submitted_at":   r.submitted_at.isoformat(),
                "weighted_total": r.weighted_total,
                "status":         r.status.name,
                "passes":         r.passes,
            }
            row.update(r.scores)
            writer.writerow(row)


def _write_json(responses: list[ScoredResponse], output_path: Path) -> None:
    """Write responses as a JSON file.

    Args:
        responses: Scored responses to serialise.
        output_path: Destination JSON file path.
    """
    data = [
        {
            "respondent_id":  r.respondent_id,
            "submitted_at":   r.submitted_at.isoformat(),
            "weighted_total": r.weighted_total,
            "status":         r.status.name,
            "passes":         r.passes,
            "scores":         r.scores,
        }
        for r in responses
    ]
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(responses: list[ScoredResponse]) -> None:
    """Print a plain-text summary table of pipeline results.

    Args:
        responses: All scored responses returned by the pipeline.
    """
    valid    = [r for r in responses if r.status == SurveyStatus.VALID]
    passing  = [r for r in valid if r.passes]
    invalid  = [r for r in responses if r.status == SurveyStatus.INVALID]
    dupes    = [r for r in responses if r.status == SurveyStatus.DUPLICATE]

    avg_score = (
        sum(r.weighted_total for r in valid) / len(valid)
        if valid else 0.0
    )

    print("\n===== Survey Pipeline Summary =====")
    print(f"  Total responses : {len(responses)}")
    print(f"  Valid           : {len(valid)}")
    print(f"  Passing         : {len(passing)}")
    print(f"  Invalid         : {len(invalid)}")
    print(f"  Duplicates      : {len(dupes)}")
    print(f"  Average score   : {avg_score:.2f} / {MAX_SCORE:.1f}")
    print("===================================\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the survey pipeline end to end."""
    logging.basicConfig(level=logging.INFO)

    input_path  = Path("survey_export.csv")
    output_path = Path("survey_results.csv")

    validators = [
        CompletenessValidator(required_keys=list(SCORE_WEIGHTS.keys())),
        RangeValidator(low=0.0, high=MAX_SCORE),
    ]
    scorer   = ResponseScorer(weights=SCORE_WEIGHTS)
    pipeline = SurveyPipeline(input_path, validators, scorer)

    responses = pipeline.run()
    write_report(responses, output_path, fmt=ReportFormat.CSV)
    print_summary(responses)
    print(f"Results written to: {output_path}")


if __name__ == "__main__":
    main()
