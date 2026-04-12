# test_r.R
#
# Longitudinal analysis of employee engagement survey data.
#
# Reads panel data covering three annual survey waves, cleans and reshapes
# it, fits a mixed-effects model to track score changes over time, produces
# departmental benchmarks, and saves publication-ready plots and a summary
# report.
#
# Dependencies:
#   install.packages(c("tidyverse", "lme4", "broom.mixed",
#                      "scales", "patchwork", "janitor"))


suppressPackageStartupMessages({
  library(tidyverse)
  library(lme4)
  library(broom.mixed)
  library(scales)
  library(patchwork)
  library(janitor)
})


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SURVEY_WAVES    <- c(2022L, 2023L, 2024L)
LIKERT_MIN      <- 1L
LIKERT_MAX      <- 5L
PASSING_SCORE   <- 3.5
OUTPUT_DIR      <- "output"
PLOT_DPI        <- 300L
PLOT_WIDTH      <- 12
PLOT_HEIGHT     <- 8
SEED            <- 42L

DIMENSIONS <- c(
  "engagement",
  "satisfaction",
  "wellbeing",
  "growth",
  "inclusion"
)

DEPT_LABELS <- c(
  "eng"    = "Engineering",
  "sales"  = "Sales",
  "ops"    = "Operations",
  "hr"     = "People & Culture",
  "fin"    = "Finance"
)


# ---------------------------------------------------------------------------
# S3 class — SurveyDataset
# ---------------------------------------------------------------------------

#' Constructor for a SurveyDataset object.
#'
#' @param data A tidy data frame with columns: employee_id, year,
#'   department, and one column per dimension.
#' @param waves Integer vector of survey years included.
#' @return An object of class SurveyDataset.
new_survey_dataset <- function(data, waves) {
  stopifnot(is.data.frame(data), is.integer(waves))
  structure(
    list(data = data, waves = waves),
    class = "SurveyDataset"
  )
}

#' Print method for SurveyDataset.
#'
#' @param x A SurveyDataset object.
#' @param ... Ignored.
print.SurveyDataset <- function(x, ...) {
  cat("SurveyDataset\n")
  cat("  Waves      :", paste(x$waves, collapse = ", "), "\n")
  cat("  Respondents:", dplyr::n_distinct(x$data$employee_id), "\n")
  cat("  Departments:", dplyr::n_distinct(x$data$department), "\n")
  cat("  Dimensions :", paste(DIMENSIONS, collapse = ", "), "\n")
  invisible(x)
}

#' Summary method for SurveyDataset.
#'
#' @param object A SurveyDataset object.
#' @param ... Ignored.
#' @return A tibble of mean scores per year and dimension.
summary.SurveyDataset <- function(object, ...) {
  object$data |>
    pivot_longer(
      cols      = all_of(DIMENSIONS),
      names_to  = "dimension",
      values_to = "score"
    ) |>
    group_by(year, dimension) |>
    summarise(
      mean_score = mean(score, na.rm = TRUE),
      sd_score   = sd(score,   na.rm = TRUE),
      n          = n(),
      .groups    = "drop"
    )
}


# ---------------------------------------------------------------------------
# Data loading and cleaning
# ---------------------------------------------------------------------------

#' Load and stack raw CSV exports for all survey waves.
#'
#' @param data_dir Path to the directory containing per-year CSVs.
#' @param waves Integer vector of years to load.
#' @return A raw combined tibble before cleaning.
load_raw_data <- function(data_dir, waves) {
  file_paths <- file.path(data_dir, paste0("survey_", waves, ".csv"))
  missing    <- file_paths[!file.exists(file_paths)]

  if (length(missing) > 0L) {
    stop("Missing data files:\n  ", paste(missing, collapse = "\n  "))
  }

  map_dfr(
    set_names(file_paths, waves),
    read_csv,
    col_types = cols(.default = col_character()),
    .id       = "wave_year"
  )
}

#' Clean, type-cast, and validate the raw combined dataset.
#'
#' Removes rows with missing employee IDs, coerces Likert responses to
#' integers, clamps values to the valid range, and standardises department
#' codes using DEPT_LABELS.
#'
#' @param raw A raw tibble as returned by load_raw_data().
#' @return A cleaned tibble with typed columns and no missing IDs.
clean_data <- function(raw) {
  raw |>
    janitor::clean_names() |>
    filter(!is.na(employee_id), employee_id != "") |>
    mutate(
      year         = as.integer(wave_year),
      employee_id  = as.character(employee_id),
      department   = tolower(str_trim(department)),
      department   = recode(department, !!!DEPT_LABELS),
      across(
        all_of(DIMENSIONS),
        ~ pmin(pmax(as.integer(.x), LIKERT_MIN), LIKERT_MAX)
      )
    ) |>
    select(employee_id, year, department, all_of(DIMENSIONS)) |>
    drop_na(all_of(DIMENSIONS))
}

#' Flag employees who responded in every survey wave.
#'
#' @param data A cleaned tibble.
#' @param waves Integer vector of all expected waves.
#' @return The same tibble with an added logical column `is_panel`.
flag_panel_respondents <- function(data, waves) {
  full_panelists <- data |>
    group_by(employee_id) |>
    summarise(wave_count = n_distinct(year), .groups = "drop") |>
    filter(wave_count == length(waves)) |>
    pull(employee_id)

  data |> mutate(is_panel = employee_id %in% full_panelists)
}


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

#' Compute an unweighted composite engagement score per row.
#'
#' @param data A cleaned tibble containing all DIMENSIONS columns.
#' @return The same tibble with an added `composite_score` column.
add_composite_score <- function(data) {
  data |>
    mutate(
      composite_score = rowMeans(pick(all_of(DIMENSIONS)), na.rm = TRUE)
    )
}

#' Classify each row into a performance band based on composite score.
#'
#' @param data A tibble with a `composite_score` column.
#' @return The same tibble with an added `band` factor column.
add_performance_band <- function(data) {
  data |>
    mutate(
      band = cut(
        composite_score,
        breaks = c(-Inf, 2, 3, 3.5, 4, Inf),
        labels = c("Critical", "At Risk", "Developing", "Strong", "Thriving"),
        right  = FALSE
      )
    )
}


# ---------------------------------------------------------------------------
# Mixed-effects modelling
# ---------------------------------------------------------------------------

#' Fit a linear mixed-effects model tracking scores over time.
#'
#' Uses employee as a random intercept to account for repeated measures,
#' and year (mean-centred) as a fixed effect to estimate the average
#' annual change in composite engagement.
#'
#' @param data A tibble with `composite_score`, `year`, and `employee_id`.
#' @return A fitted lmerMod object.
fit_longitudinal_model <- function(data) {
  panel <- data |>
    filter(is_panel) |>
    mutate(year_c = year - mean(year))

  lmer(
    composite_score ~ year_c + (1 | employee_id),
    data    = panel,
    REML    = TRUE,
    control = lmerControl(optimizer = "bobyqa")
  )
}

#' Extract tidy fixed-effect estimates from a fitted model.
#'
#' @param model A fitted lmerMod object.
#' @return A tibble of term, estimate, std.error, statistic, and p.value.
extract_fixed_effects <- function(model) {
  broom.mixed::tidy(model, effects = "fixed", conf.int = TRUE)
}


# ---------------------------------------------------------------------------
# Benchmarking
# ---------------------------------------------------------------------------

#' Compute departmental benchmarks for the latest survey wave.
#'
#' @param data A tibble with composite_score, department, year, and band.
#' @return A tibble ranked by mean composite score descending.
compute_department_benchmarks <- function(data) {
  latest_wave <- max(data$year)

  data |>
    filter(year == latest_wave) |>
    group_by(department) |>
    summarise(
      n               = n(),
      mean_score      = mean(composite_score, na.rm = TRUE),
      median_score    = median(composite_score, na.rm = TRUE),
      pct_thriving    = mean(band == "Thriving", na.rm = TRUE),
      pct_at_risk     = mean(band %in% c("At Risk", "Critical"), na.rm = TRUE),
      .groups         = "drop"
    ) |>
    mutate(
      exceeds_target  = mean_score >= PASSING_SCORE,
      rank            = dense_rank(desc(mean_score))
    ) |>
    arrange(rank)
}


# ---------------------------------------------------------------------------
# Visualisation
# ---------------------------------------------------------------------------

#' Build a trend line plot of composite scores by year and department.
#'
#' @param data A tibble with year, department, and composite_score.
#' @return A ggplot object.
plot_score_trends <- function(data) {
  trend_data <- data |>
    group_by(year, department) |>
    summarise(mean_score = mean(composite_score, na.rm = TRUE), .groups = "drop")

  ggplot(trend_data, aes(x = year, y = mean_score, colour = department)) +
    geom_line(linewidth = 0.9) +
    geom_point(size = 2.5) +
    geom_hline(
      yintercept = PASSING_SCORE, linetype = "dashed",
      colour = "grey50", linewidth = 0.6
    ) +
    scale_x_continuous(breaks = SURVEY_WAVES) +
    scale_y_continuous(limits = c(LIKERT_MIN, LIKERT_MAX), breaks = 1:5) +
    scale_colour_brewer(palette = "Set2") +
    labs(
      title    = "Composite engagement score by department",
      subtitle = paste("Dashed line =", PASSING_SCORE, "passing threshold"),
      x        = "Survey year",
      y        = "Mean composite score",
      colour   = "Department"
    ) +
    theme_minimal(base_size = 12) +
    theme(legend.position = "bottom")
}

#' Build a bar chart of performance band distribution per department.
#'
#' @param data A tibble with department and band columns.
#' @return A ggplot object.
plot_band_distribution <- function(data) {
  latest_wave <- max(data$year)

  band_data <- data |>
    filter(year == latest_wave) |>
    count(department, band) |>
    group_by(department) |>
    mutate(pct = n / sum(n)) |>
    ungroup()

  ggplot(band_data, aes(x = department, y = pct, fill = band)) +
    geom_col(position = "stack") +
    scale_y_continuous(labels = percent_format()) +
    scale_fill_manual(
      values = c(
        "Thriving"   = "#2ecc71",
        "Strong"     = "#82e0aa",
        "Developing" = "#f4d03f",
        "At Risk"    = "#e67e22",
        "Critical"   = "#c0392b"
      )
    ) +
    labs(
      title = paste("Performance band distribution —", latest_wave),
      x     = NULL,
      y     = "Proportion of respondents",
      fill  = "Band"
    ) +
    theme_minimal(base_size = 12) +
    theme(
      axis.text.x  = element_text(angle = 30, hjust = 1),
      legend.position = "right"
    )
}


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

#' Save plots and a CSV benchmark table to the output directory.
#'
#' @param trend_plot A ggplot trend line plot.
#' @param band_plot A ggplot band distribution plot.
#' @param benchmarks A tibble of departmental benchmarks.
#' @param output_dir Path to the output directory.
write_report <- function(trend_plot, band_plot, benchmarks, output_dir) {
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

  combined <- trend_plot / band_plot +
    plot_annotation(
      title   = "Employee Engagement Survey — Longitudinal Report",
      caption = paste("Generated:", format(Sys.time(), "%Y-%m-%d %H:%M"))
    )

  ggsave(
    filename = file.path(output_dir, "engagement_report.png"),
    plot     = combined,
    dpi      = PLOT_DPI,
    width    = PLOT_WIDTH,
    height   = PLOT_HEIGHT
  )

  write_csv(
    benchmarks,
    file.path(output_dir, "departmental_benchmarks.csv")
  )

  message("Report written to: ", output_dir)
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main <- function() {
  set.seed(SEED)
  message("Loading survey data...")

  raw  <- load_raw_data(data_dir = "data", waves = SURVEY_WAVES)
  data <- raw |>
    clean_data() |>
    flag_panel_respondents(waves = SURVEY_WAVES) |>
    add_composite_score() |>
    add_performance_band()

  dataset <- new_survey_dataset(data, waves = SURVEY_WAVES)
  print(dataset)

  message("Fitting longitudinal model...")
  model  <- fit_longitudinal_model(data)
  fx     <- extract_fixed_effects(model)
  message("Fixed effects:")
  print(fx)

  benchmarks <- compute_department_benchmarks(data)
  message("Departmental benchmarks (latest wave):")
  print(benchmarks)

  trend_plot <- plot_score_trends(data)
  band_plot  <- plot_band_distribution(data)
  write_report(trend_plot, band_plot, benchmarks, output_dir = OUTPUT_DIR)
}

main()
