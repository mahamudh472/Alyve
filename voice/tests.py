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

	@patch("voice.quote_generation.generate_reply")
	def test_personal_quotes_generated_on_subsequent_update(self, mock_generate_reply):
		# Create loved one with missing fields
		loved_one = LovedOne.objects.create(
			user=self.user,
			description="Missing fields first",
		)

		quotes = Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL)
		self.assertEqual(quotes.count(), 0)
		self.assertFalse(mock_generate_reply.called)

		# Now add the necessary fields
		mock_generate_reply.return_value = SimpleNamespace(
			text='["Love is all around.", "Always in our hearts.", "Warm memory here."]'
		)

		loved_one.name = "Maya"
		loved_one.relationship = "Mother"
		loved_one.save()

		quotes = Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL).order_by("created_at")
		self.assertEqual(quotes.count(), 3)
		self.assertEqual(quotes[0].content, "Love is all around.")
		self.assertEqual(mock_generate_reply.call_count, 1)

	@patch("voice.quote_generation.generate_reply")
	def test_personal_quotes_not_regenerated_if_already_exist(self, mock_generate_reply):
		mock_generate_reply.return_value = SimpleNamespace(
			text='["First quote.", "Second quote.", "Third quote."]'
		)

		loved_one = LovedOne.objects.create(
			user=self.user,
			name="Maya",
			relationship="Mother",
		)

		quotes = Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL)
		self.assertEqual(quotes.count(), 3)
		self.assertEqual(mock_generate_reply.call_count, 1)

		# Reset the mock to verify it doesn't get called again
		mock_generate_reply.reset_mock()

		# Update another field
		loved_one.description = "Updated description"
		loved_one.save()

		# Verify mock was not called and quotes are still present
		self.assertFalse(mock_generate_reply.called)
		quotes = Quote.objects.filter(loved_one=loved_one, quote_type=Quote.QuoteScope.PERSONAL)
		self.assertEqual(quotes.count(), 3)


from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import Plan, UserSubscription, SubscriptionCloneUsage

@override_settings(VOICE_APP={"ELEVENLABS_API_KEY": "test_key", "ELEVENLABS_BASE_URL": "https://api.elevenlabs.io"})
class LovedOneCloningTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(email="cloner@example.com", password="pass12345", full_name="Cloner User")
		self.plan = Plan.objects.create(name="Premium", price=75.00, clone_limit=2)
		self.subscription = UserSubscription.objects.create(user=self.user, plan=self.plan, is_active=True)

	@patch("voice.views._maybe_clone_eleven_voice")
	@patch("voice.quote_generation.generate_personal_quotes_for_loved_one")
	def test_create_loved_one_with_voice_file_success(self, mock_generate_quotes, mock_clone):
		mock_clone.return_value = "eleven_voice_123"
		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		query = """
		mutation CreateLovedOne($voiceFile: Upload!) {
			createOrUpdateLovedOne(
				name: "Maya"
				relationship: "Mother"
				voiceFile: $voiceFile
			) {
				id
				name
				elevenVoiceId
			}
		}
		"""
		result = schema.execute_sync(
			query,
			variable_values={"voiceFile": voice_file},
			context_value={"request": SimpleNamespace(user=self.user)},
		)

		self.assertIsNone(result.errors)
		self.assertEqual(result.data["createOrUpdateLovedOne"]["name"], "Maya")
		self.assertEqual(result.data["createOrUpdateLovedOne"]["elevenVoiceId"], "eleven_voice_123")

		# Verify LovedOne in database
		loved_one = LovedOne.objects.get(name="Maya", user=self.user)
		self.assertEqual(loved_one.eleven_voice_id, "eleven_voice_123")

		# Verify clone usage recorded
		self.assertEqual(SubscriptionCloneUsage.objects.filter(subscription=self.subscription).count(), 1)

	@patch("voice.views._maybe_clone_eleven_voice")
	@patch("voice.quote_generation.generate_personal_quotes_for_loved_one")
	def test_create_loved_one_with_voice_file_failure_cleanup(self, mock_generate_quotes, mock_clone):
		error_json = '{"detail":{"status":"quota_exceeded","message":"This request exceeds your quota."}}'
		mock_clone.side_effect = RuntimeError(f"ElevenLabs clone failed: 401 {error_json}")
		
		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		query = """
		mutation CreateLovedOne($voiceFile: Upload!) {
			createOrUpdateLovedOne(
				name: "Maya"
				relationship: "Mother"
				voiceFile: $voiceFile
			) {
				id
				name
			}
		}
		"""
		result = schema.execute_sync(
			query,
			variable_values={"voiceFile": voice_file},
			context_value={"request": SimpleNamespace(user=self.user)},
		)

		self.assertIsNotNone(result.errors)
		self.assertIn("Voice cloning failed: This request exceeds your quota.", result.errors[0].message)

		# Verify LovedOne was cleaned up (deleted)
		self.assertFalse(LovedOne.objects.filter(name="Maya", user=self.user).exists())

	@patch("voice.views._maybe_clone_eleven_voice")
	@patch("voice.quote_generation.generate_personal_quotes_for_loved_one")
	def test_create_loved_one_clone_limit_exceeded(self, mock_generate_quotes, mock_clone):
		lo1 = LovedOne.objects.create(user=self.user, name="Maya1")
		lo2 = LovedOne.objects.create(user=self.user, name="Maya2")
		SubscriptionCloneUsage.objects.create(subscription=self.subscription, loved_one=lo1)
		SubscriptionCloneUsage.objects.create(subscription=self.subscription, loved_one=lo2)

		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		query = """
		mutation CreateLovedOne($voiceFile: Upload!) {
			createOrUpdateLovedOne(
				name: "Maya3"
				relationship: "Mother"
				voiceFile: $voiceFile
			) {
				id
			}
		}
		"""
		result = schema.execute_sync(
			query,
			variable_values={"voiceFile": voice_file},
			context_value={"request": SimpleNamespace(user=self.user)},
		)

		self.assertIsNotNone(result.errors)
		self.assertEqual(result.errors[0].message, "Voice clone limit reached for your subscription plan.")
		
		# Verify the new loved_one was cleaned up (deleted)
		self.assertFalse(LovedOne.objects.filter(name="Maya3", user=self.user).exists())

