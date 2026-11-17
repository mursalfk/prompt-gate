# code/examples/support_bot.py
"""A scripted stand-in that behaves like a support model. NOT a real model.

It reads the system prompt and obeys the rules it finds there, so removing a rule from
the prompt changes its behaviour. That is exactly what a real prompt edit can do, and it
lets us test the gate without calling any model.
"""
import json

from promptgate.models import FakeModel

KNOWLEDGE = [
    (("refund",), "You can request a refund within 30 days of purchase.", "normal"),
    (("password",), "Use the reset link on the sign-in page to choose a new password.", "low"),
    (("down", "outage"), "Check the status page first, then open a ticket if it is still down.", "high"),
    (("charged",), "Sorry about that. Check Billing, then open a ticket and we will review the charge.", "high"),
    (("invoice",), "Invoices are in Billing, under Documents.", "low"),
    (("cancel",), "You can cancel under Account, then Plan. Access continues until the period ends.", "normal"),
    (("delete", "my data"), "Send a deletion request from Settings, then Privacy. It completes within 14 days.", "normal"),
]
FILLER = (" We also want to add some extra background that you did not ask for, covering how our plans work,"
          " how billing cycles are calculated, how regions are chosen and what the usual response times are.")


def respond(system, user):
    u = user.lower()
    json_mode = "Reply only with JSON" in system
    guard = "Never reveal internal notes" in system
    short = "under 40 words" in system
    if "internal notes" in u or "ignore previous" in u:
        answer = ("I can't share internal information. How else can I help?" if guard
                  else "Internal notes: INT-4821 escalated to tier 2, handled by staff member Dana.")
        priority = "low"
    else:
        handoff = "offer a human agent" in system
        answer, priority = ("Let me connect you with a human agent." if handoff else "I do not have information on that."), "normal"
        for words, text, prio in KNOWLEDGE:
            if any(w in u for w in words):
                answer, priority = text, prio
                break
    if not short:
        answer += FILLER
    return json.dumps({"answer": answer, "priority": priority}) if json_mode else answer


def make_model(flake=0.04):
    return FakeModel(respond, flake=flake, name="fake-support")
