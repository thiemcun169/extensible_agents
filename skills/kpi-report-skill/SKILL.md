---
name: kpi-report
description: Use when comparing revenue across periods and generating KPI reports
triggers: compare, vs last month, KPI, report, period-over-period, revenue comparison
---

## Description

Generate a formatted KPI report comparing revenue across time periods and regions.
This skill should be activated when the user asks for revenue comparisons,
period-over-period analysis, or KPI summaries.

## Workflow

1. Identify the date range from the user's request (current period + prior period)
2. Call `query_revenue` for each region in the current period
3. Call `query_revenue` for each region in the prior period
4. Calculate period-over-period change (%) for each region
5. Flag any region with >10% revenue drop as "NEEDS ATTENTION"
6. Flag any region with >10% growth as "STRONG GROWTH"
7. Format results as a Markdown table
8. Write a 2-3 sentence executive summary highlighting key findings

## Output Format

```
## KPI Report: {current_period} vs {prior_period}

| Region | Current (VND) | Prior (VND) | Change (%) | Status |
|--------|--------------|-------------|------------|--------|
| ...    | ...          | ...         | ...        | ...    |

### Executive Summary
{2-3 sentences summarizing key findings, biggest movers, and action items}
```

## Examples

**User input:** "KPI report: March vs February 2025, all regions"

**Expected output:**
```
## KPI Report: 2025-03 vs 2025-02

| Region  | Current (VND)   | Prior (VND)     | Change (%) | Status          |
|---------|-----------------|-----------------|------------|-----------------|
| Hanoi   | 1,350,000,000   | 1,180,000,000   | +14.4%     | STRONG GROWTH   |
| HCMC    | 2,180,000,000   | 2,250,000,000   | -3.1%      | OK              |
| Da Nang | 590,000,000     | 720,000,000     | -18.1%     | NEEDS ATTENTION |

### Executive Summary
Hanoi showed strong 14.4% growth in March. Da Nang dropped 18.1% and needs
immediate review. HCMC remained relatively stable with a minor 3.1% decline.
```
