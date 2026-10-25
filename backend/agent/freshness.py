"""Questions whose answer may have changed since the model was trained.

Measured on politics / current events (officeholders, latest race results,
rates): when the agent searched it was right 54/60 times; when it answered
from its own knowledge it was right 8/15 - "As of October 2026, the President
is Joe Biden", "the current Pope is Pope Francis". So for these questions the
agent's first round must call a tool (tool_choice "required"); the model still
picks which one, and every later round is unconstrained.
"""
from __future__ import annotations
# (also: unknown_entities - names the model may not know; see below)

import re
import time

_FRESH_RE = re.compile(
    # explicit time / recency ("current" only next to something that changes in the world - see below)
    r"\b(latest|newest|today|tonight|tomorrow|yesterday|right now|this (week|month|year|season)"
    r"|last (night|week|month|weekend)|recent(ly)?|most recent|news|breaking|so far|nowadays)\b"
    # "still"/"anymore" only about something in the world ("Is Biden still president?", "Is Python 2 still
    # supported?"), not "it still fails" / "refactor so it is still fast"
    r"|\bstill\s+(?:\w+\s+){0,2}?(?:the\s+)?(president|prime minister|pm|chancellor|pope|king|queen|ceo|leader|mayor"
    r"|governor|senator|champion|coach|manager|owner|in office|in charge|alive|open|available|on sale|supported|maintained"
    r"|called|named|running|leading|in business|operating|banned|legal|free)\b"
    r"|\b(?:is|are|does|do)\b[^.?!]{1,40}\b(?:anymore|any more)\b"
    r"|\bcurrent(ly)?\s+(?:\w+\s+){0,2}?(price|prices|rate|rates|version|release|status|weather|forecast|score|standings"
    r"|leader|president|prime minister|pm|chancellor|pope|ceo|cfo|cto|mp|mayor|governor|champion|record|population|events?"
    r"|news|government|holder|senator|representative|congress(?:wo)?m[ae]n|justice|cabinet|minister)s?\b"
    # values that move
    r"|\b(price|prices|stock price|exchange rate|weather|forecast|score|standings|polls?|polling|election|elections|midterms?"
    r"|primaries|primary (?:election|race|results?)|won(?!['\u2019]t)|winner)\b"
    r"|\bupcoming (?:\w+\s+){0,2}?(?:elections?|races?|votes?|launch(?:es)?|releases?|match(?:es)?|games?|fights?|events?)\b"
    r"|\b(interest|exchange|deposit|mortgage|inflation|unemployment|tax|policy|refinancing|facility|base)\s+rates?\b"
    r"|\b(election|race|match|game|vote|poll)\s+results?\b"
    # releases and schedules ("Is Tailscale 1.102 out yet?", "Has Ubuntu 26.10 been released?", "When is the next
    # SpaceX launch?", "How much is a Raspberry Pi 5 these days?", "What did the Fed decide on rates?")
    r"|\b(these days|at the moment|out yet|released yet|been released|been announced)\b"
    r"|\b(?:is|are|has|have)\b[^?]{0,50}\b(?:out|released|launched)\s*(?:yet\s*)?\?"
    r"|\bnext\s+(?:\w+\s+){0,2}?(launch|event|election|game|match|race|keynote|release|fight|eclipse)\b"
    r"|\bwho(?:'s| is| are)\s+(?:leading|winning|ahead|top|bottom)\b"
    r"|\b(fed|federal reserve|ecb|central bank|bank of \w+|boe|boj|rba)\b[^.?]{0,40}\brates?\b"
    r"|\b(?:open|closed)\s+(?:for|to)\s+(?:traffic|visitors|the public)\b|\b(?:cost|costs|price|prices)\s+now\b"
    # who holds an office or title now ("Who is the prime minister of Japan?")
    r"|\bwho(?:'s| is| are)\s+(?:the\s+)?(?:current\s+)?(president|prime minister|premier|chancellor|pope|king|queen"
    r"|monarch|ceo|chair(man|woman|person)?|leader|head of|governor|mayor|speaker|secretary[- ]general|minister"
    r"|champion|coach|manager|owner)s?\b",
    re.I,
)

# Questions about Pantheon itself or the user's own things are never "look it up on
# the web": "describe the current configuration (of this agent harness)" was answered
# from Windows and Microsoft Agent Framework pages because "current" forced a search
# (2026-10-02). The agent has its own tools for these (get_self_documentation,
# recall, list_artifacts, ...).
_SELF_RE = re.compile(
    r"\b(?:your|yourself|yourselves)\s+(?:own\s+)?(?:config(?:uration)?|settings?|setup|set-up|tools?|capabilit(?:y|ies)|memor(?:y|ies)"
    r"|instructions?|system prompt|prompt|model|models|version|skills?|name|personality|persona|limits?|features?|architecture)\b"
    r"|\babout (?:you|yourself)\b"
    r"|\bthe current (?:config(?:uration)?|setup|set-up|settings)\b(?!\s+(?:of|for|in|on)\b)"
    r"|\b(?:what|which) (?:tools?|models?|llms?|skills?|capabilit(?:y|ies)|version|memory|settings?) (?:do|are|can|did|have) you\b"
    r"|\b(?:are|is) you running\b|\byou(?:'re| are)? running on\b|\bwhat are you running\b"
    r"|\b(?:this|the) (?:agent|assistant|harness|agent harness|system|bot|chat|conversation|session|project|workspace|server"
    r"|setup|deployment|instance|app|tool)\b"
    r"|\bpantheon\b"
    r"|\bmy (?:own )?(?:projects?|files?|notes?|memor(?:y|ies)|config(?:uration)?|settings?|setup|tasks?|jobs?|artifacts?"
    r"|workspace|account|data|conversations?|chats?|documents?|uploads?)\b",
    re.I,
)


# Questions about what Pantheon can do / how it works ("Can you compile Swift?",
# "Where do files I upload go?", "What's the most tool steps you can take?",
# "Which model is answering me?"). Without the self-doc the agent answered them
# from general LLM knowledge: "no hard-coded limit" (it is 100 rounds), uploads
# "go to the workspace" (they are artifacts), "I'll message you on Telegram"
# (not configured). Short questions only - a request with its own material
# ("Can you summarize this: ...") is work, not a question about Pantheon.
_CAPABILITY_RE = re.compile(
    r"^\W*(?:can|could|do|does|are|will|would|is it possible for) you\b[^?]{0,90}\b(?:compile|build|run|execute|push|"
    r"commit|message|notify|ping|email|text me|remember|schedule|browse|click|access|connect|upload|isolat\w*|sandbox\w*"
    r"|send (?:me|you)|see my|read my|generate (?:images?|pictures?)|keep|store|save)\b"
    r"(?!\s+(?:me\s+)?(?:a|an|some|this|these|that|those|it|the following)\b)"
    r"|\b(?:how (?:long|much|many)|what'?s the (?:most|max\w*|limit|longest|biggest))\b[^?]{0,80}\b(?:you|your|yours)\b"
    r"|\bwhere (?:do|does|did|will|are|is)\b(?=[^?]{0,60}\b(?:I|my|you|your|files?|uploads?|artifacts?|notes?|outputs?|results?)\b)"
    r"[^?]{0,60}\b(?:go|end up|get (?:saved|stored)|saved|stored|kept)\b"
    r"|\bwhat happens (?:if|when) I (?:ask|tell|want|have|let|give|upload|send) you\b"
    r"|\bif I (?:ask|tell|want) you to\b[^?]{0,90}\bwhat (?:happens|do you do|would you do)\b"
    r"|\bwhat (?:do|would|will) you do (?:if|when) I\b"
    r"|\b(?:which|what) (?:model|llm|search engines?)\b[^?]{0,40}\b(?:you|answer\w*|is this|am I)\b"
    r"|\b(?:which|what) (?:model|llm)s? (?:are|is) (?:you|answering|running)\b"
    r"|\bswitch (?:which|the|your) model\b"
    r"|\byour (?:workspace|artifacts?|memory|limits?|sandbox|skills)\b"
    r"|\b(?:do|does|will) (?:my )?(?:messages?|data|chats?|conversations?) (?:get )?(?:sent|go|leave)\b",
    re.I,
)
_MAX_CAPABILITY_Q = 220


def asks_about_capabilities(message: str) -> bool:
    m = (message or "").strip()
    return len(m) <= _MAX_CAPABILITY_Q and bool(_CAPABILITY_RE.search(m))


def about_self(message: str) -> bool:
    return bool(_SELF_RE.search(message or ""))


# Programming requests: "price", "score", "latest", "news", "weather" there name variables, columns and
# components ("Write a function that returns the price", "What is the score function in sklearn?", "get the
# latest order per customer") - a web search for those words only adds noise. Only an explicit question about
# a version/release still counts ("What's the latest version of FastAPI? I want to pin it").
_CODE_CTX_RE = re.compile(
    r"```|`[^`\n]+`|\b\w+\(\)|\b[a-z]+_[a-z_]+\b|Traceback|\b\w+(?:Error|Exception)\b|^\W*implement"
    r"|\b(?:functions?|methods?|variables?|columns?|quer(?:y|ies)|regexe?s?|loops?|component|endpoints?|scripts?"
    r"|unit tests?|test cases?|(?:the|my|a) tests?|bugs?|compiles?|compiler|parser|dataframes?|stack ?trace|refactor\w*"
    r"|debug\w*|widgets?|structs?|enums?|arrays?|dicts?|json|sql|api|commits?|cron|bash|async|await|callbacks?"
    r"|type hints|dataclass(?:es)?|numpy|sklearn|django|flask|responsive|css|html|schema|database|syntax"
    r"|log files?|timestamps?|on ci|fix (?:it|this|that)|button|frontend|backend|ui|ux|navbar|modal|dropdown)\b"
    r"|\ban? \w+ field\b|\brepo(?:s|sitory)?\b(?!\s+rates?)|\b(?<!zip )(?<!area )(?<!dress )(?<!promo )(?<!postal )code\b"
    r"|(?<!first )(?<!world )(?<!middle )(?<!business )(?<!economy )\bclass(?:es)?\b(?!\s+(?:of|tickets?|seats?|flights?|fares?|action|war))",
    re.I,
)
_VERSION_Q_RE = re.compile(
    r"\b(?:latest|newest|current|most recent)\s+(?:\w+\s+){0,2}?(?:versions?|releases?)\b|\bout yet\b|\bbeen released\b",
    re.I,
)


# The user's own past and small talk: "What did I tell you yesterday about my knee?", "Remind me what we decided
# last week", "We won our pub quiz last night!", "I'm tired today" - time words, but nothing on the web.
_PERSONAL_RE = re.compile(
    r"\b(?:i|we)\s+(?:(?:just|already|have|had)\s+)?(?:told|tell you|said|say to you|mention(?:ed)?|decided?|talked?|discuss(?:ed)?"
    r"|logged|noted|saved|agreed?|planned|wrote down)\b"
    r"|\bdid (?:i|we)\s+(?:tell|say|mention|decide|talk|discuss|log|note|save|agree|plan)\b"
    r"|\bremind me what\b|\b(?:do|did) you remember\b|\bhow(?:'s| is) it going\b|\bhow are you\b"
    r"|\bmy (?:calendar|schedule|agenda|to-?do(?: list)?|inbox|e-?mails?|orders?|reminders?|appointments?|meetings?"
    r"|diary|journal|habits?|goals?|workouts?|runs?)\b",
    re.I,
)
# a first-person statement, not a question or request ("My team won! So happy", "I won't be around tomorrow")
_STATEMENT_RE = re.compile(r"^\W*(?:i|i'm|i've|i'll|i'd|we|we're|we've|my|our)\b(?![^?]*\?)(?!.*\b(?:need|want|look up|find"
                           r"|check|search|tell me|wonder|curious|know)\b)", re.I | re.S)


# Creative writing: "Write a poem about the news of a baby's birth", "Draft an email telling my team we won the
# contract", "Write a fictional news report". (Not "write a summary of today's news" - that one needs the news.)
_CREATIVE_RE = re.compile(
    r"^\W*(?:(?:can|could|would) you\s+|please\s+|help me\s+)?(?:write|draft|compose|rewrite|proofread|polish|edit|make)\b"
    r"(?=[^.?!]{0,60}\b(?:poems?|haiku|story|stories|songs?|lyrics|limerick|toast|speech|eulogy|letter|e-?mail|message"
    r"|(?:blog |linkedin )?post|intro(?:duction)?|press release|paragraph|essay|tweet|caption|slogan|joke|fictional"
    r"|imaginary|made[- ]up|this|that|it)\b)",
    re.I,
)


def personal_context(message: str) -> bool:
    m = message or ""
    return bool(_PERSONAL_RE.search(m) or _STATEMENT_RE.search(m) or _CREATIVE_RE.search(m))


# How things work in general ("Explain how an interest rate swap works", "How do elections work in a
# parliamentary system?", "What's the difference between price elasticity and ...") and word problems ("If the
# price of an apple is $2 ...", "A team won 18 of 30 games ...") use the moving-value words without asking for
# today's value. There only an explicit time word (latest, today, this week, current, recently ...) counts.
_GENERAL_RE = re.compile(
    r"^\W*(?:can you |please )?explain\b|\bhow (?:do|does|did|is|are|would|can)\b[^?]{0,60}\bwork(?:s|ed)?\b"
    r"|\bwhat(?:'s| is| are) the differences? between\b|\bhow (?:is|are) [^?]{0,40}\b(?:calculated|computed|measured|defined)\b"
    r"|\bin (?:a|an) (?:z-test|t-test|regression|model|algorithm|formula|equation)\b"
    r"|^\W*(?:if|suppose|assume|say|a|an)\b(?=[^?]*\d)",
    re.I,
)
_TIME_WORD_RE = re.compile(
    r"\b(latest|newest|today|tonight|tomorrow|yesterday|right now|this (week|month|year|season)|last (night|week|month"
    r"|weekend)|recent(ly)?|most recent|current(ly)?|so far|nowadays|these days|at the moment|upcoming)\b",
    re.I,
)


def coding_context(message: str) -> bool:
    return bool(_CODE_CTX_RE.search(message or ""))


# "... save it to an artifact for my notes" says where the answer goes, not what it's about: "compare the
# current prices of Bitcoin and Ethereum ... for my notes" was never looked up.
_SAVE_TO_MINE_RE = re.compile(r"\b(?:for|to|into)\s+my\s+(?:own\s+)?notes?\b", re.I)


# A question about this year or the next ("What are the most competitive governor races in 2026?") is about
# events after training. Only in a question, and not "my 2026 budget".
def _year_question(message: str) -> bool:
    now = time.localtime().tm_year
    if "?" not in message:
        return False
    return any(now - 1 <= int(y) <= now + 2 for y in re.findall(r"(?<!my )(?<!our )\b(20\d\d)\b", message, re.I))


def needs_fresh_facts(message: str) -> bool:
    m = message or ""
    if not (_FRESH_RE.search(m) or _year_question(m)) or about_self(_SAVE_TO_MINE_RE.sub(" ", m)):
        return False
    if coding_context(m) and not _VERSION_Q_RE.search(m):
        return False
    if personal_context(m):
        return False
    if _GENERAL_RE.search(m) and not _TIME_WORD_RE.search(m):
        return False
    return True


# ── Names the model may not know ─────────────────────────────────────────────
#
# Products, models and companies announced after training. Measured: asked
# "What is Jev?" (TypeSafe AI's decision model, Sept 2026) the agent searched
# 1 time in 5 and otherwise made something up from whatever was nearby -
# "Jevons paradox", "the user's Proton Mail project", and for "use Jev to
# summarize my meeting notes", "an artifact-based summarization skill". Jev
# can't generate text at all. So a capitalised name the agent is asked about,
# or asked to use, counts as time-sensitive unless it is one of Pantheon's own
# tools/skills.

# Capitalised ("Jev", "Steam Frame") or camel-case ("iPhone Duo", "macOS"), up to 4 words incl. versions ("v2.0")
_NAME = r"(?P<name>(?:[A-Z]|[a-z]+[A-Z])[\w.+\-]*(?:\s+(?:[A-Z0-9][\w.+\-]*|v?\d[\w.]*)){0,3})"
_ENTITY_RES = [
    re.compile(r"\b(?i:what|who)(?:'s|\s+(?i:is|are|was|were))\s+(?:(?i:the|a|an)\s+)?" + _NAME),
    re.compile(r"\b(?i:tell me about|explain|describe|have you heard (?:of|about)|do you know(?: about)?|how does|"
               r"how do i use|what can|capabilities of|features of|pricing (?:of|for)|reviews? of|specs? (?:of|for))\s+"
               r"(?:(?i:the|a|an)\s+)?" + _NAME),
    re.compile(r"\b(?i:use|using|try|trying|set up|setting up|integrate|integrating|install|installing|switch(?:ing)? to|"
               r"buy|buying|adopt|migrate to|upgrade to|deploy|deploying)\s+(?:(?i:the|a|an)\s+)?" + _NAME),
    # the name as the subject of a question about it: "What operating system does a Googlebook run?", "Will the new
    # Googlebooks run ChromeOS?", "Is Fugu Ultra v2.0 a single model?", "Explain what Pydantic AI 2 is"
    re.compile(r"\b(?i:does|do|will|would|can|could)\s+(?:(?i:a|an|the|my|our|new|the new)\s+)?" + _NAME
               + r"\s+(?i:run|runs|support|work|cost|fit|include|come with|replace)\b"),
    re.compile(r"\b(?i:explain|tell me|describe)\s+(?i:what|who)\s+(?:(?i:the|a|an)\s+)?" + _NAME + r"\s+(?i:is|are|does)\b"),
]
# "Is X a ...?" only counts for a versioned name ("Is Fugu Ultra v2.0 a single model?"): "Is Lisbon a good place
# to live?" isn't about something new.
_VERSIONED_ENTITY_RE = re.compile(r"(?:^|[.!?]\s+)(?i:is|are)\s+(?:(?i:the|a|an)\s+)?" + _NAME + r"\s+(?i:a|an|the)\s")
# capitalised words that aren't names of things to look up
_COMMON = set("""I I'm I've Me My We Our You Your It Its This That These Those The A An Here There Hi Hello Thanks
    Please OK Okay Yes No Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May
    June July August September October November December""".split())


# Long-established names the model knows well: "Can you use Python to parse this?", "Explain Rust lifetimes",
# "Install Redis on Ubuntu" are not questions about something new. Only the bare name counts - "Python 3.15",
# "Rust 2027" or "Kubernetes 1.37" (a version) are still looked up. Multi-word names joined with "_".
WELL_KNOWN = frozenset(n.replace("_", " ").lower() for n in """
    Python Rust Go Golang Java JavaScript TypeScript C C++ C# Ruby PHP Perl Swift Kotlin Scala Haskell Elixir Erlang
    Clojure Lua R Julia Dart Bash Zsh PowerShell SQL HTML CSS WebAssembly Wasm Fortran COBOL Lisp OCaml F# MATLAB
    Node Node.js Deno React Vue Angular Svelte Next.js Nuxt jQuery Django Flask FastAPI Rails Ruby_on_Rails Spring Laravel
    Express Pandas NumPy SciPy Matplotlib Jupyter PyTorch TensorFlow Keras scikit-learn Sklearn Pydantic SQLAlchemy Celery
    Docker Docker_Compose Kubernetes K8s Helm Terraform Ansible Puppet Vagrant Podman Nginx Apache Caddy Traefik
    Linux Ubuntu Debian Fedora CentOS RHEL Alpine Windows macOS iOS Android FreeBSD Unix
    Git GitHub GitHub_Actions GitLab Bitbucket Jenkins CircleCI Vim Neovim Emacs VS_Code Visual_Studio IntelliJ Xcode
    PostgreSQL Postgres MySQL MariaDB SQLite MongoDB Redis Elasticsearch Cassandra Kafka RabbitMQ DynamoDB
    AWS Azure GCP S3 EC2 Cloudflare Heroku Vercel Netlify Firebase Stripe PayPal Twilio Slack Discord Zoom
    REST REST_API GraphQL gRPC JSON YAML XML HTTP HTTPS TCP UDP DNS SSH SSL TLS OAuth OAuth_2.0 JWT CORS CSRF XSS
    Webpack Vite Babel ESLint Prettier npm pip Poetry Conda Homebrew CMake Gradle Maven Cargo
    Excel PowerPoint Photoshop Markdown LaTeX Arduino Raspberry_Pi
    Big_O Fourier Bayesian Newton Einstein Darwin Shakespeare Turing Alan_Turing Ada_Lovelace Marie_Curie
    Isaac_Newton Albert_Einstein Charles_Darwin Napoleon French_Revolution World_War_II Roman_Empire
    Builder Singleton Factory Observer MVC SOLID TDD Agile Scrum
""".split())


def unknown_entities(message: str, known: set[str] | None = None) -> list[str]:
    """Names in an entity question / "use X" request, minus Pantheon's own tools/skills (``known``, lower-case)."""
    if about_self(message):
        return []
    known = {k.lower() for k in (known or set())}
    out = []
    for rx in (*_ENTITY_RES, _VERSIONED_ENTITY_RE):
        for m in rx.finditer(message or ""):
            name = m.group("name").strip(" .?!,")
            if rx is _VERSIONED_ENTITY_RE and not re.search(r"\d", name):
                continue
            first = name.split()[0]
            bare = re.sub(r"['\u2019]s$", "", name).lower()
            if first in _COMMON or bare in WELL_KNOWN or name.lower() in known or name.lower().replace(" ", "_") in known:
                continue
            if name not in out:
                out.append(name)
    return out


# Self-description questions get Pantheon's real state first: with only the web
# search suppressed, "describe the current configuration of this agent harness"
# was still answered from a recalled earlier (wrong) reply, without any tool.
_SELF_DESCRIBE_RE = re.compile(
    r"\b(config(?:uration|ured)?|settings?|set ?up|architecture|tools?|capabilit(?:y|ies)|version|models?|"
    r"memory (?:system|tiers?|setup)|how (?:are|is) (?:you|it|this|pantheon) (?:set up|configured|built|running))\b",
    re.I,
)


def wants_self_description(message: str) -> bool:
    return (about_self(message) and bool(_SELF_DESCRIBE_RE.search(message or ""))) or asks_about_capabilities(message)


# ── Follow-ups inherit the lookup ────────────────────────────────────────────
#
# "can you do a similar breakdown for the house?" right after two searched
# questions about the Senate races has no time words of its own, so it went to
# the quick route unsearched, filled a 435-seat table from memory until
# max_tokens and repeated "Greg Landsman" across Ohio districts (2026-10-02).
# "and Docker Engine?" after "latest Kubernetes version" answered v27.1.1
# (2024). A short follow-up in a conversation about current facts is itself
# about current facts - unless it's an acknowledgement or asks to reshape the
# answer already given.
_ACK_RE = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|k|thanks?|thank you|thx|ty|cheers|cool|great|nice|perfect|got it|awesome|sounds good|"
    r"makes sense|good|wow|lol|haha|interesting|i see|understood|noted|sure|yes|yep|yeah|no|nope|bye|so|very|much"
    r"|that'?s|it|helpful|all|for now)\W*)+$",
    re.I,
)
# reshaping what was already said needs no new facts
_RESHAPE_RE = re.compile(
    r"\b(summari[sz]e|rewrite|rephrase|reword|translate|shorter|longer|shorten|simplif\w*|format|bullet|table of that"
    r"|tl;?dr|poem|joke|in (?:french|spanish|german|english)|explain (?:that|this|it)|eli5|why did you)\b",
    re.I,
)
FOLLOWUP_MAX_CHARS = 300
FOLLOWUP_LOOKBACK = 3   # user messages


def is_ack(message: str) -> bool:
    return bool(_ACK_RE.match((message or "").strip()))


# an answer that ended with a Sources list (ours - core.py - or the model's own) was looked up
_SOURCED_RE = re.compile(r"(?im)^\W*sources?\W*$[\s\S]*?https?://")


def looked_up(reply: str) -> bool:
    return bool(_SOURCED_RE.search(reply or ""))


def followup_needs_fresh(message: str, history: list[dict]) -> bool:
    """True when ``message`` continues a conversation about current facts and so
    needs a lookup too. ``history`` = this session's earlier messages
    ({"role", "content"}, oldest first). A user question that needed fresh facts,
    or an answer built on web sources, makes the conversation "current"; it stays
    so through short follow-ups and acknowledgements, and a long or self-directed
    message ends it."""
    m = (message or "").strip()
    if (not m or len(m) > FOLLOWUP_MAX_CHARS or is_ack(m) or about_self(m) or _RESHAPE_RE.search(m)
            or needs_fresh_facts(m)):
        return False
    users = [i for i, h in enumerate(history) if h.get("role") == "user"][-FOLLOWUP_LOOKBACK:]
    live = False
    for n, i in enumerate(users):
        p = str(history[i].get("content") or "")
        end = users[n + 1] if n + 1 < len(users) else len(history)
        replies = " ".join(str(h.get("content") or "") for h in history[i + 1:end] if h.get("role") == "assistant")
        if needs_fresh_facts(p) or looked_up(replies):
            live = True
        elif about_self(p) or len(p) > FOLLOWUP_MAX_CHARS:
            live = False
    return live


# ── Connected MCP tools that cover a time-sensitive question ──────────────────

# Words too common in tool descriptions to mean the tool covers the question.
_LOOKUP_STOPWORDS = frozenset("""
about access after again against also always another any around available based before being between both
called cannot comprehensive current data date details each either every first from given have here information
into just latest like list more most next number only other over please provide provided provides query
real realtime real-time recent request requests result results return returns same search service should since
some specific such supports than that their them then there these this those through time today tool tools under
until update updates upon user users using value values what when where which while will with within without would
your yours
""".split())
_WORD_RE = re.compile(r"[a-z][a-z-]{3,}")


def _lookup_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _LOOKUP_STOPWORDS}


def mcp_tools_for(message: str, tools: list[dict]) -> list[str]:
    """Names of the mcp_* tools whose description (or name) shares a
    distinctive word with the question - "weather" for an Xweather forecast
    tool. When one exists, a time-sensitive question is left to round 1 so
    the model can call it instead of a pre-emptive web search; the round-1
    guard still falls back to the web if the model looks nothing up."""
    words = _lookup_words(message)
    if not words:
        return []
    hits: list[str] = []
    for t in tools or []:
        fn = t.get("function") or {}
        name = str(fn.get("name") or "")
        if not name.startswith("mcp_"):
            continue
        haystack = _lookup_words(f"{name.replace('_', ' ')} {fn.get('description') or ''}")
        if words & haystack:
            hits.append(name)
    return hits
