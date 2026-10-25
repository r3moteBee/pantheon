# Weather Forecast

Get weather data from a connected weather tool when one exists, from a web search only when none does, and present it as a clean, scannable daily forecast. The output must render well in both Telegram (plain text/markdown) and a web chat UI — no HTML tables.

## Procedure

1. **Determine location.** Use the location the user specified. If none was given, check semantic memory for a default/home location. If still unknown, ask.

2. **Determine timeframe.** Default to 5 days starting from today unless the user asked for something specific (e.g., "this weekend", "next 3 days", "tomorrow").

3. **Get the data — connected weather tool first.** Look through your tools for one from a weather service (an `mcp_*` tool whose description mentions weather, forecast or conditions — for example an Xweather, OpenWeather or NWS server). If there is one:
   - Call its forecast tool for the location and timeframe (a daily forecast for multi-day questions, current conditions or an hourly forecast for "right now" / "this afternoon").
   - Do NOT run a web search first, and do not run one afterwards to "double-check" — the service is the authoritative source. Only fall back to the web if the tool returns an error, no data for that location, or is not available.
   - If the tool gives highs/lows but not wind, humidity or feels-like, a second call to the same service's current-conditions or hourly tool is fine; a web search is not.

4. **Fallback — web search.** Only when no weather tool is connected or it failed: run a web search like `"weather forecast [location] next 5 days"`. If the first search doesn't return enough detail (missing wind, humidity, or feels-like), run one follow-up search targeting the missing data.

5. **Format the forecast** using the output format below. Extract as much of the following as the data provides for each day — don't fabricate data that wasn't returned. Say which source it came from only if the user asks or the sources disagree.

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

- Use Fahrenheit by default for US locations, Celsius for everywhere else. If the user has a stated preference in memory, use that. A weather tool may return one unit — convert rather than mixing units in one forecast.
- Keep it compact. Each day should be 4-6 lines max. People glance at weather, they don't read essays.
- If the data is sparse (e.g., only highs and conditions), show what you have rather than nothing. Don't pad with made-up numbers.
- Round temperatures to whole numbers.
- Include a one-line advisory at the end if conditions warrant it (e.g., "Bring an umbrella Wednesday" or "Wind chill makes Thursday feel well below freezing").
- If the user asked a yes/no question like "will it rain tomorrow?", answer that directly first, then show the relevant forecast data.
