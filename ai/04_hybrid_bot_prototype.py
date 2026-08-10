# Databricks notebook source
# MAGIC %md
# MAGIC # Module 3 -- Hybrid Bot Prototype (Claude-Powered Intent)
# MAGIC
# MAGIC Upgrade of the rule-based draft: `detect_intent()` now calls Claude first, with the
# MAGIC original regex classifier kept as an automatic fallback if the API call fails or
# MAGIC returns something unparseable. That fallback is the actual "hybrid" part of the
# MAGIC design -- not just AI-with-a-rules-baseline-for-comparison, but AI-with-a-safety-net.
# MAGIC
# MAGIC **Not everything was moved to Claude.** Negation detection and menu-selection
# MAGIC detection stay as fast regex checks -- there's no language-understanding benefit to
# MAGIC routing "cancel" or "3" through an API call, only latency and cost. Only intent
# MAGIC detection, where free-text phrasing actually varies, goes through Claude.
# MAGIC
# MAGIC Intent taxonomy matches `03_conversation_classifier.py` (banking, card, loan,
# MAGIC insurance, callback, technical_issue, other) so results are comparable across both
# MAGIC notebooks rather than using a different set of categories here.
# MAGIC
# MAGIC Runs on 6 hardcoded test messages -- cheap enough that no sampling/cost-estimate step
# MAGIC is needed here, unlike Module 2.

# COMMAND ----------

# MAGIC %run ./01_claude_api_setup.py

# COMMAND ----------

import re

INTENT_CATEGORIES = ["banking", "card", "loan", "insurance", "callback", "technical_issue", "other"]

INTENT_PROMPT_TEMPLATE = """Classify the customer's intent in this single WhatsApp message. Respond with
ONLY one word, exactly one of: banking, card, loan, insurance, callback, technical_issue, other

Message: "{message}"
"""


class HybridChatbot:
    def __init__(self, use_ai_intent: bool = True):
        self.use_ai_intent = use_ai_intent
        self.context = {
            'negation_count': 0,
            'last_intent': None,
            'messages': []
        }

        # Fallback only -- used when the Claude call fails or returns something unparseable.
        self.intent_patterns = {
            'banking': r'balance|how much|check account|debit order|stop order',
            'card': r'\bcard\b|lost card|stolen|\bpin\b',
            'loan': r'\bloan\b|apply|credit|finance|settlement|pay.*off',
            'insurance': r'funeral|claim|death|policy|beneficiary|cover',
            'callback': r'call me|phone me|speak to|agent|help',
            'technical_issue': r'not working|error|cant access|wont open|frozen',
        }

    def _detect_intent_rules(self, message: str) -> str:
        """Regex fallback -- also usable standalone for the AI-vs-rules comparison."""
        message_lower = message.lower()
        for intent, pattern in self.intent_patterns.items():
            if re.search(pattern, message_lower):
                return intent
        return 'other'

    def _detect_intent_ai(self, message: str) -> tuple[str, bool]:
        """Returns (intent, used_fallback). Falls back to rules on any API or parsing failure."""
        prompt = INTENT_PROMPT_TEMPLATE.format(message=message)
        try:
            text, _was_cached = call_claude(prompt, max_tokens=10)
            intent = text.strip().lower().strip(".")
            if intent in INTENT_CATEGORIES:
                return intent, False
            print(f"  [AI returned unrecognized intent '{intent}' -- falling back to rules]")
            return self._detect_intent_rules(message), True
        except Exception as e:
            print(f"  [Claude call failed ({e}) -- falling back to rules]")
            return self._detect_intent_rules(message), True

    def detect_intent(self, message: str) -> str:
        if self.use_ai_intent:
            intent, _used_fallback = self._detect_intent_ai(message)
            return intent
        return self._detect_intent_rules(message)

    def is_negation(self, message: str) -> bool:
        """Fast rule-based check -- no need for an API call here."""
        return bool(re.search(r'\b(cancel|no|nope|stop)\b', message.lower()))

    def is_menu_selection(self, message: str) -> bool:
        """Fast rule-based check -- no need for an API call here."""
        return bool(re.match(r'^[0-9]{1,2}$', message.strip()))

    def get_frustration_score(self) -> int:
        score = self.context['negation_count'] * 30
        if self.context['messages']:
            last_msg = self.context['messages'][-1]
            if last_msg.isupper():
                score += 20
            if any(phrase in last_msg.lower() for phrase in ['not working', 'no answer', 'frustrated']):
                score += 25
        return min(score, 100)

    def process_message(self, user_message: str) -> dict:
        self.context['messages'].append(user_message)

        if self.is_negation(user_message):
            self.context['negation_count'] += 1
            if self.context['negation_count'] >= 2:
                return {
                    'response': "I notice you're having trouble finding what you need.\n\n"
                               "Let me connect you with a specialist who can help.\n\n"
                               "Reply YES for immediate callback "
                               "(we'll call within 30 mins during business hours)",
                    'type': 'escalation'
                }
            return {
                'response': "No problem! You can:\n"
                           "1. Return to main menu\n"
                           "2. Ask me a question\n"
                           "3. Request callback\n\n"
                           "What would you like to do?",
                'type': 'navigation'
            }

        if self.is_menu_selection(user_message):
            return {
                'response': f"You selected option {user_message}. Processing...",
                'type': 'menu'
            }

        intent = self.detect_intent(user_message)
        self.context['last_intent'] = intent

        responses = {
            'banking': {
                'response': "I can help with banking -- balance, transfers, debit orders.\n\n"
                           "Reply YES to proceed, or type MENU for other options.",
                'type': 'direct_action',
            },
            'card': {
                'response': "Card services:\n\n"
                           "1. Report lost/stolen card\n"
                           "2. Card activation\n"
                           "3. PIN services\n\n"
                           "Select 1-3 or type your question",
                'type': 'guided_menu',
            },
            'loan': {
                'response': "Loans and settlements:\n\n"
                           "1. Apply for a loan\n"
                           "2. Check settlement amount\n"
                           "3. Speak to a specialist\n\n"
                           "Select 1-3 or describe your issue",
                'type': 'guided_menu',
            },
            'insurance': {
                'response': "For insurance/funeral claims:\n\n"
                           "- Call: 0860 123 000\n"
                           "- Required: Policy number, ID, Death certificate\n\n"
                           "Type CALLBACK if you'd like us to phone you.",
                'type': 'information',
            },
            'callback': {
                'response': "I'll arrange a callback.\n\n"
                           "We'll contact you within 30 minutes during business hours.\n\n"
                           "Reply YES to confirm.",
                'type': 'callback',
            },
            'technical_issue': {
                'response': "Sorry you're running into a technical issue.\n\n"
                           "1. Try logging out and back in\n"
                           "2. Report a bug\n"
                           "3. Speak to a specialist\n\n"
                           "Select 1-3",
                'type': 'guided_menu',
            },
        }

        if intent in responses:
            result = dict(responses[intent])
            result['intent'] = intent
            return result

        return {
            'response': f"I understand you're asking about: '{user_message[:40]}...'\n\n"
                       "I can help with:\n"
                       "1. Banking services (balance, transfers)\n"
                       "2. Card issues (lost, PIN, activation)\n"
                       "3. Loans and applications\n"
                       "4. Speak to someone (callback)\n\n"
                       "Select 1-4 or rephrase your question",
            'type': 'clarification',
            'intent': 'other',
            'frustration_score': self.get_frustration_score()
        }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Demo -- rule-based vs AI intent, side by side
# MAGIC
# MAGIC Two bot instances process the same 6 messages: one AI-first (with fallback), one
# MAGIC rules-only. Printing both intents per message makes the upgrade visible instead of
# MAGIC just trusting it happened.

# COMMAND ----------

test_messages = [
    "Please help me cancel a debit order",
    "I need to check my balance",
    "cancel",  # negation
    "My card is lost",
    "This is not working!",
    "cancel",  # second negation -- should trigger escalation
]

ai_bot = HybridChatbot(use_ai_intent=True)
rules_bot = HybridChatbot(use_ai_intent=False)

print("\n" + "=" * 80)
print("DEMO: rule-based vs Claude-powered intent detection")
print("=" * 80)

for i, message in enumerate(test_messages, 1):
    print(f"\n[Message {i}] User: {message}")

    ai_response = ai_bot.process_message(message)
    rules_response = rules_bot.process_message(message)

    ai_intent = ai_response.get('intent', f"(type: {ai_response['type']})")
    rule_intent = rules_response.get('intent', f"(type: {rules_response['type']})")
    match = "same" if ai_intent == rule_intent else "DIFFERENT"

    print(f"  Rule-based intent: {rule_intent}")
    print(f"  Claude intent:     {ai_intent}   [{match}]")
    print(f"  Bot response: {ai_response['response'][:120]}")
    if 'frustration_score' in ai_response:
        print(f"  Frustration: {ai_response['frustration_score']}/100")
    print("-" * 80)

print("\n" + "=" * 80)
print("HYBRID BOT DEMO COMPLETE")
print("=" * 80)

# COMMAND ----------

print("""
WHAT ACTUALLY CHANGED FROM THE RULE-BASED DRAFT:

- detect_intent() now calls Claude first; regex only fires as a fallback on API
  failure or an unparseable response -- see the [falling back to rules] print
  above if that happened during this run.
- Intent taxonomy aligned with 03_conversation_classifier.py (banking, card, loan,
  insurance, callback, technical_issue, other) instead of a separate ad hoc set.
- Negation and menu-selection detection deliberately stayed rule-based -- these are
  exact-match checks with no ambiguity, so an API call would only add latency/cost
  with no accuracy benefit.

For a real accuracy comparison (not just "did they agree"), see the labeling
export in 03_conversation_classifier.py -- the same caveat applies here: this demo
shows the two approaches disagreeing or agreeing, not which one is "correct."
""")