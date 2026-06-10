"""Fitness domain: workout plans, Obsidian note generation, and next-day logic."""
from __future__ import annotations

from src.domain.fitness.plan import (
    WORKOUT_PLAN,
    WORKOUT_DAYS,
    is_rest_day,
    get_training_day,
    get_weekday_name,
)
from src.domain.fitness.generator import (
    generate_workout_note,
    workout_daily_link,
    upsert_fitness_section,
)
from src.domain.fitness.parser import WorkoutSummary, parse_workout_note
from src.domain.fitness.next_day import next_workday_logic

__all__ = [
    "WORKOUT_PLAN",
    "WORKOUT_DAYS",
    "is_rest_day",
    "get_training_day",
    "get_weekday_name",
    "generate_workout_note",
    "workout_daily_link",
    "upsert_fitness_section",
    "WorkoutSummary",
    "parse_workout_note",
    "next_workday_logic",
]
