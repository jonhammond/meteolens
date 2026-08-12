# OPEN-METEO API DATA TO TARGET

1. Core Atmosphere & Temperature:
   - temperature_2m (Air temperature 2 meters above ground)
   - apparent_temperature ("Feels like" temperature factoring wind and humidity)
   - relative_humidity_2m (Relative atmospheric humidity percentage)

2. Precipitation & Sky Conditions:
   - precipitation (Total liquid precipitation depth in millimeters)
   - cloud_cover (Total cloud cover percentage)
   - weather_code (WMO weather interpretation code)

3. Wind Dynamics:
   - wind_speed_10m (Wind speed 10 meters above ground)
   - wind_gusts_10m (Maximum wind gusts 10 meters above ground)

Additional API Configurations:

- The script should target the "current" data parameters block.
- Pass the latitude and longitude dynamically via parameters.
- Include the "timezone=auto" parameter to align timestamps with local query times.

## HOW THIS DATA WILL BE USED IN THE POWER BI REPORT

Design the database schema and data types keeping these specific Power BI dashboard visual requirements in mind:

- Time-Series Trends: A continuous date/time log (`recorded_at` TIMESTAMP) is required to plot multi-line charts tracking fluctuations in temperature vs. apparent temperature over hours, days, and weeks.
- Wind Variance Area Charts: A dual-axis area chart mapping `wind_speed_10m` against `wind_gusts_10m` to visually isolate sudden wind spikes and storm patterns over time.
- Climate Relationship Scatter Plots: Correlation charts plotting `temperature_2m` against `relative_humidity_2m` to identify heat-index trends and atmospheric moisture thresholds.
- Conditional KPI Cards: High-level KPI metric cards showing the latest current values for precipitation, temperature, and cloud cover. These will use DAX measures to apply conditional background colors (e.g., turning blue when precipitation > 0).
- Categorical Slicers: A descriptive text translation column mapping the numeric `weather_code` values (e.g., transforming code 0 to "Clear Sky", code 61 to "Rain") so users can filter the entire dashboard by specific weather conditions.
