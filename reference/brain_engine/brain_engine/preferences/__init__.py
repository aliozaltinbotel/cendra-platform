"""Adaptive Preferences Engine — learns owner rules from approval decisions.

After each approved/denied action, the system asks the owner follow-up questions
to learn their preferences (scope, conditions, frequency). These rules are stored
and used to auto-approve or auto-deny future similar actions.
"""

from brain_engine.preferences.models import PreferenceRule, RuleScope
from brain_engine.preferences.store import PreferenceStore
from brain_engine.preferences.learner import PreferenceLearner
from brain_engine.preferences.enforcer import PolicyEnforcer

__all__ = [
    "PreferenceRule",
    "RuleScope",
    "PreferenceStore",
    "PreferenceLearner",
    "PolicyEnforcer",
]
