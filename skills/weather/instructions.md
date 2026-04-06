# Weather Forecast

Fetch weather data via web search and present it as a clean, scannable daily forecast. The output must render well in both Telegram (plain text/markdown) and a web chat UI — no HTML tables.

## Procedure

1. **Determine location.** Use the location the user specified. If none was given, check semantic memory for a default/home location. If still unknown, ask.

2. **Determine timeframe.** Default to 5 days starting from today unless the user asked for something specific (e.g., "this weekend", "next 3 days", "tomorrow").

3. **Search for weather.** Run a web search like `"weather forecast [location] next 5 days"`. If the first search doesn't return enough detail (missing wind, humidity, or feels-like), run a follow-up search targeting the missing data.

4. **Format the forecast** using the output format below. Extract as much of the following as the search results provide for each day — don't fabricate data that wasn't in the results.

## Output Format

Start with a location header and current conditions if available, then list each day.

```
Weather for [City, State/Country]
As of [date and time if available]

Now: [temp]° [condition emoji] [condition]
Feels like [feels-like temp]° · Wind [speed] [direction] · Humidity [pct]%

---

[Day of week, Month Day]
  High [high]° / Low [low]°
  [condition emoji] [condition description]
  Wind: [speed] [direction]
  Feels like: [morning feels-like]° morning, [afternoon feels-like]° afternoon
  Humidity: [pct]%
  [Precipitation: chance% if relevant]

[Day of week, Month Day]
  High [high]° / Low [low]°
  ...
```

## Condition Emojis

Pick the best match for each day's primary condition:

- ☀️  Clear / Sunny
- 🌤️  Mostly sunny
- ⛅  Partly cloudy
- 🌥️  Mostly cloudy
- ☁️  Overcast
- 🌦️  Showers / Sun and rain
- 🌧️  Rain
- ⛈️  Thunderstorms
- 🌨️  Snow
- 🌫️  Fog / Haze
- 💨  Windy (use as secondary indicator if winds are notable)
- 🧊  Freezing rain / Ice

## Important

- Use Fahrenheit by default for US locations, Celsius for everywhere else. If the user has a stated preference in memory, use that.
- Keep it compact. Each day should be 4-6 lines max. People glance at weather, they don't read essays.
- If search results are sparse (e.g., only highs and conditions), show what you have rather than nothing. Don't pad with made-up numbers.
- Round temperatures to whole numbers.
- Include a one-line advisory at the end if conditions warrant it (e.g., "Bring an umbrella Wednesday" or "Wind chill makes Thursday feel well below freezing").
- If the user asked a yes/no question like "will it rain tomorrow?", answer that directly first, then show the relevant forecast data.
