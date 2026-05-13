from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from accounts.models import User
from main.schema import schema

from .models import LovedOne, Quote


class QuoteGenerationTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(email="test@example.com", password="pass12345", full_name="Test User")

	@patch("voice.quote_generation.generate_reply")
	def test_personal_quotes_are_generated_for_new_loved_one(self, mock_generate_reply):
		mock_generate_reply.return_value = SimpleNamespace(
			text='["You still light up every room.", "Love never leaves, it just changes form.", "I carry your name with me."]'
		)

		loved_one = LovedOne.objects.create(
			user=self.user,
			name="Maya",
			relationship="Mother",
			nickname_for_user="kiddo",
			description="Warm, funny, and deeply kind.",
			speaking_style="Gentle and direct.",
		)

		quotes = Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL).order_by("created_at")
		self.assertEqual(quotes.count(), 3)
		self.assertEqual(quotes[0].content, "You still light up every room.")

	@patch("voice.quote_generation.generate_reply")
	def test_personal_quotes_are_generated_when_openai_adds_trailing_text(self, mock_generate_reply):
		mock_generate_reply.return_value = SimpleNamespace(
			text='["You still light up every room.", "Love never leaves, it just changes form.", "I carry your name with me."]\n\nHope these help.'
		)

		loved_one = LovedOne.objects.create(
			user=self.user,
			name="Maya",
			relationship="Mother",
			nickname_for_user="kiddo",
			description="Warm, funny, and deeply kind.",
			speaking_style="Gentle and direct.",
		)

		quotes = Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL).order_by("created_at")
		self.assertEqual(quotes.count(), 3)
		self.assertEqual(quotes[1].content, "Love never leaves, it just changes form.")

	@patch("voice.quote_generation.generate_reply")
	def test_quote_query_prefers_personal_quotes(self, mock_generate_reply):
		mock_generate_reply.return_value = SimpleNamespace(
			text='["Generated quote 1", "Generated quote 2", "Generated quote 3"]'
		)

		loved_one = LovedOne.objects.create(user=self.user, name="Maya", relationship="Mother")
		Quote.objects.create(
			user=self.user,
			loved_one=loved_one,
			quote_type=Quote.QuoteScope.PERSONAL,
			content="Personal quote",
		)
		Quote.objects.create(
			quote_type=Quote.QuoteScope.GLOBAL,
			content="Global quote",
		)

		result = schema.execute_sync(
			"""
			query {
			  quote {
				content
				quoteType
			  }
			}
			""",
			context_value={"request": SimpleNamespace(user=self.user)},
		)

		self.assertIsNone(result.errors)
		self.assertEqual(result.data["quote"]["quoteType"], "personal")
		self.assertIn(
			result.data["quote"]["content"],
			{"Personal quote", "Generated quote 1", "Generated quote 2", "Generated quote 3"},
		)

	def test_quote_query_falls_back_to_global_quotes(self):
		Quote.objects.create(
			quote_type=Quote.QuoteScope.GLOBAL,
			content="Global quote",
		)

		result = schema.execute_sync(
			"""
			query {
			  quote {
				content
				quoteType
			  }
			}
			""",
			context_value={"request": SimpleNamespace(user=self.user)},
		)

		self.assertIsNone(result.errors)
		self.assertEqual(result.data["quote"]["content"], "Global quote")
		self.assertEqual(result.data["quote"]["quoteType"], "global")
