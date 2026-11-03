"""Grok planning agent: Grok gets the whole planner as tools and works step by step.

Every tool call is reported through `on_trace` so the map app can show the
agent's reasoning live. Without an API key, a scripted plan runs the same tools
in a fixed order so the demo still works.
"""
import json

from . import grok

MAX_STEPS = 10

TOOLS = [
    {"type": "function", "function": {
        "name": "set_scenario",
        "description": "Describe the flood in plain words (place or river, rainfall or severity); it is parsed and simulated. Call only when the user describes a new flood.",
        "parameters": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]}}},
    {"type": "function", "function": {
        "name": "simulate_outage",
        "description": "Expected people without cell service and share of towers dark at each hour as the flood rises and recedes.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "pick_sites",
        "description": "Choose terminal sites (maximal covering). alpha 0 = most people, 2 = most vulnerable. hour = which hour's outage to plan for.",
        "parameters": {"type": "object", "properties": {
            "k": {"type": "integer"}, "alpha": {"type": "number"}, "hour": {"type": "number"}}}}},
    {"type": "function", "function": {
        "name": "route_trucks",
        "description": "Plan delivery routes on the road network with flooded roads removed. Also reports how long the same deliveries take if trucks leave before the water rises (prepositioned).",
        "parameters": {"type": "object", "properties": {"n_trucks": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "what_if",
        "description": "Compare the current plan with a variation (more terminals, another truck, other objective or hour) without changing the plan.",
        "parameters": {"type": "object", "properties": {
            "k": {"type": "integer"}, "alpha": {"type": "number"}, "hour": {"type": "number"}, "n_trucks": {"type": "integer"}}}}},
    {"type": "function", "function": {
        "name": "get_site",
        "description": "Details on one site by id or name: why it was or was not chosen.",
        "parameters": {"type": "object", "properties": {"site": {"type": "string"}}, "required": ["site"]}}},
]

SYSTEM = """You are the planning agent inside Starlink Drop, a tool that helps a county emergency manager place
a limited number of Starlink internet terminals when a flood knocks out cell service.

The current flood is already loaded: {scenario}. Only call set_scenario if the user describes a NEW flood with
a place or river; never make one up. Otherwise plan for the current flood.

Work step by step with the tools: look at the outage over time, pick sites, route the trucks, and check one
what-if only if it would change the decision. Every number you state must come from a tool result.

Finish with plain spoken English, 80 to 120 words, no lists or abbreviations (write full site names):
the top three sites, how many people and vulnerable people the plan reconnects, how that compares with placing
the same terminals at the sites closest to the flooding, when all terminals are running (and how much sooner if
trucks leave before the water rises), and how many people are still unreached."""


class Agent:
    def __init__(self, planner, on_trace=None):
        self.p = planner
        self.on_trace = on_trace or (lambda e: None)

    # ---------- tools ----------
    def run_tool(self, name, args):
        p = self.p
        if name == "set_scenario":
            sc, info = grok.parse_scenario(args["description"])
            p.reset(sc)
            return {"scenario": sc, "parsed_by": info["used"]}
        if name == "simulate_outage":
            return {"hours": p.sim["hours"],
                    "people_without_service": [round(float(x)) for x in p.sim["people_out"]],
                    "share_of_towers_dark": [round(float(x), 3) for x in p.sim["towers_dark_share"]]}
        if name == "pick_sites":
            st = p.replan(reason="agent picked sites", k=args.get("k"), alpha=args.get("alpha"), hour=args.get("hour"))
            pl = st["plan"]
            return {"k": pl["k"], "alpha": pl["alpha"], "hour": pl["hour"], "metrics": pl["metrics"],
                    "one_per_county_office": pl["baseline_metrics"], "closest_to_damage": pl["nearest_metrics"],
                    "optimality": pl.get("optimality"),
                    "sites": [{k: s[k] for k in ("site_id", "name", "type", "people_reached", "vulnerable_reached")}
                              for s in pl["sites"]]}
        if name == "route_trucks":
            st = p.replan(reason="agent routed trucks", n_trucks=args.get("n_trucks"))
            r = st["routes"]
            if not r:
                return {"error": "road network not loaded"}
            return {"n_trucks": r["n_trucks"], "all_terminals_running_by_min": r["done_min"],
                    "unreachable_sites": r["unreachable"], "blocked_road_segments": r["blocked_road_segments"],
                    "people_reconnected_by_hour": r["reconnected_by_hour"],
                    "prepositioned": r.get("prepositioned"),
                    "stops": [{"truck": x["truck"], "stops": x["stops"]} for x in r["routes"]]}
        if name == "what_if":
            return self._what_if(args)
        if name == "get_site":
            q = args["site"]
            info = p.site(q)
            if info is None:
                hits = p.find_site(q)
                info = p.site(hits[0]["site_id"]) if hits else {"error": f"no site matching '{q}'"}
            return info
        return {"error": f"unknown tool {name}"}

    def _what_if(self, a):
        p = self.p
        base = p.plan["metrics"]
        alt = p.opt.plan(p.sim, p.scenario, k=a.get("k") or p.k,
                         alpha=a.get("alpha") if a.get("alpha") is not None else p.alpha,
                         hour=min(p.sim["hours"], key=lambda h: abs(h - float(a.get("hour") or p.hour))),
                         closed=p.closed, site_bonus=p.site_bonus)
        out = {"current": base, "variation": alt["metrics"], "variation_settings": a}
        if a.get("n_trucks") and p.roads:
            r = p.roads.plan_routes(p.scenario, p.depot, alt["sites"], int(a["n_trucks"]))
            out["variation_all_running_by_min"] = max((x["duration_min"] for x in r["routes"]), default=0)
            out["current_all_running_by_min"] = p.routes["done_min"] if p.routes else None
        return out

    # ---------- loop ----------
    def run(self, message):
        self.on_trace({"type": "trace", "kind": "user", "text": message})
        if not grok.has_key():
            return self._scripted(message)
        system = SYSTEM.format(scenario=self.p.scenario.get("name", "a flood"))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": message}]
        for step in range(MAX_STEPS):
            try:
                msg = grok.chat(messages, TOOLS, timeout=45)
            except Exception as e:      # API slow or down: finish with the scripted plan
                self.on_trace({"type": "trace", "kind": "tool_result", "tool": "grok",
                               "summary": f"Grok didn't respond ({e.__class__.__name__}); finishing offline"})
                return self._scripted(message)
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = (msg.get("content") or "").strip()
                self.on_trace({"type": "trace", "kind": "answer", "text": answer})
                return answer
            for c in calls:
                name = c["function"]["name"]
                args = json.loads(c["function"]["arguments"] or "{}")
                self.on_trace({"type": "trace", "kind": "tool_call", "tool": name, "args": args, "step": step + 1})
                try:
                    result = self.run_tool(name, args)
                except Exception as e:
                    result = {"error": str(e)}
                self.on_trace({"type": "trace", "kind": "tool_result", "tool": name, "summary": _short(result)})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result, default=float)})
        answer = "I reached my step limit; the map shows the latest plan."
        self.on_trace({"type": "trace", "kind": "answer", "text": answer})
        return answer

    def _scripted(self, message):
        """Offline fallback: same tools, fixed order."""
        steps = [("set_scenario", {"description": message})] if grok.mentions_place(message) else []
        steps += [("simulate_outage", {}),
                 ("pick_sites", {"alpha": 2, "hour": 12}), ("route_trucks", {"n_trucks": 2}),
                 ("what_if", {"n_trucks": 3})]
        results = {}
        for i, (name, args) in enumerate(steps, 1):
            self.on_trace({"type": "trace", "kind": "tool_call", "tool": name, "args": args, "step": i})
            results[name] = self.run_tool(name, args)
            self.on_trace({"type": "trace", "kind": "tool_result", "tool": name, "summary": _short(results[name])})
        answer, _ = grok.briefing(self.p.summary())
        self.on_trace({"type": "trace", "kind": "answer", "text": answer})
        return answer


def _short(result):
    """One-line summary of a tool result for the trace panel."""
    if "error" in result:
        return f"error: {result['error']}"
    if "people_without_service" in result:
        peak = max(result["people_without_service"])
        return f"peak {peak:,} people without service"
    if "metrics" in result:
        m = result["metrics"]
        return f"{result['k']} sites reach {m['people_reached']:,.0f} people ({m['vulnerable_reached']:,.0f} vulnerable)"
    if "all_terminals_running_by_min" in result:
        return (f"{result['n_trucks']} trucks, all running by {result['all_terminals_running_by_min']:.0f} min, "
                f"{len(result['unreachable_sites'])} unreachable")
    if "variation" in result:
        return (f"variation reaches {result['variation']['people_reached']:,.0f} vs "
                f"{result['current']['people_reached']:,.0f} now")
    if "scenario" in result:
        return result["scenario"].get("name", "scenario set")
    if "name" in result:
        return f"{result['name']}: {result.get('people_without_service_in_reach', 0):,} people without service in reach"
    return "done"
