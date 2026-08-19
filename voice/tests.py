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
from rest_framework.test import APIClient

@override_settings(VOICE_APP={"ELEVENLABS_API_KEY": "test_key", "ELEVENLABS_BASE_URL": "https://api.elevenlabs.io"})
class LovedOneCloningTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(email="cloner@example.com", password="pass12345", full_name="Cloner User")
		self.plan = Plan.objects.create(name="Premium", price=75.00, clone_limit=2)
		self.subscription = UserSubscription.objects.create(user=self.user, plan=self.plan, is_active=True)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	@patch("main.views._maybe_clone_eleven_voice")
	def test_create_loved_one_with_voice_file_success(self, mock_clone):
		mock_clone.return_value = "eleven_voice_123"
		loved_one = LovedOne.objects.create(user=self.user, name="Maya", relationship="Mother")
		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		response = self.client.post(
			"/api/v1/loved-one/voice-upload/",
			{"id": loved_one.id, "voice_file": voice_file},
			format="multipart"
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["message"], "Voice file uploaded successfully")
		self.assertEqual(response.data["data"]["id"], loved_one.id)

		# Verify LovedOne in database
		loved_one.refresh_from_db()
		self.assertEqual(loved_one.eleven_voice_id, "eleven_voice_123")

		# Verify clone usage recorded
		self.assertEqual(SubscriptionCloneUsage.objects.filter(subscription=self.subscription).count(), 1)

	@patch("main.views._maybe_clone_eleven_voice")
	def test_create_loved_one_with_voice_file_no_subscription(self, mock_clone):
		mock_clone.return_value = "eleven_voice_123"
		self.subscription.delete()

		loved_one = LovedOne.objects.create(user=self.user, name="Maya", relationship="Mother")
		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		response = self.client.post(
			"/api/v1/loved-one/voice-upload/",
			{"id": loved_one.id, "voice_file": voice_file},
			format="multipart"
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["message"], "Voice file uploaded successfully")

		# Verify LovedOne in database
		loved_one.refresh_from_db()
		self.assertEqual(loved_one.eleven_voice_id, "eleven_voice_123")

		# Verify clone usage is not recorded (since no subscription exists)
		self.assertEqual(SubscriptionCloneUsage.objects.count(), 0)

	@patch("main.views._maybe_clone_eleven_voice")
	def test_create_loved_one_with_voice_file_failure_cleanup(self, mock_clone):
		error_json = '{"detail":{"status":"quota_exceeded","message":"This request exceeds your quota."}}'
		mock_clone.side_effect = RuntimeError(f"ElevenLabs clone failed: 401 {error_json}")
		
		# Test with a brand new loved one id (is_new = True path)
		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		response = self.client.post(
			"/api/v1/loved-one/voice-upload/",
			{"id": 9999, "voice_file": voice_file},
			format="multipart"
		)

		self.assertEqual(response.status_code, 400)
		self.assertIn("Voice cloning failed: This request exceeds your quota.", response.data["error"])

		# Verify LovedOne was cleaned up (deleted) because it was new
		self.assertFalse(LovedOne.objects.filter(id=9999).exists())

	@patch("main.views._maybe_clone_eleven_voice")
	def test_create_loved_one_clone_limit_exceeded(self, mock_clone):
		lo1 = LovedOne.objects.create(user=self.user, name="Maya1")
		lo2 = LovedOne.objects.create(user=self.user, name="Maya2")
		SubscriptionCloneUsage.objects.create(subscription=self.subscription, loved_one=lo1)
		SubscriptionCloneUsage.objects.create(subscription=self.subscription, loved_one=lo2)

		loved_one = LovedOne.objects.create(user=self.user, name="Maya3")
		voice_file = SimpleUploadedFile("sample.wav", b"dummy audio content", content_type="audio/wav")

		response = self.client.post(
			"/api/v1/loved-one/voice-upload/",
			{"id": loved_one.id, "voice_file": voice_file},
			format="multipart"
		)

		self.assertEqual(response.status_code, 403)
		self.assertEqual(response.data["error"], "Voice clone limit reached for your subscription plan.")
		
		# Verify loved_one was not deleted because it was not new (already existed in DB before upload)
		self.assertTrue(LovedOne.objects.filter(id=loved_one.id).exists())

	def test_upload_loved_one_avatar(self):
		loved_one = LovedOne.objects.create(user=self.user, name="Maya", relationship="Mother")
		# 1x1 valid GIF image bytes
		avatar_bytes = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
		avatar_file = SimpleUploadedFile("avatar.gif", avatar_bytes, content_type="image/gif")

		response = self.client.post(
			"/api/v1/loved-one/avatar-upload/",
			{"id": loved_one.id, "avatar": avatar_file},
			format="multipart"
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["message"], "Avatar uploaded successfully")
		self.assertIsNotNone(response.data["data"]["avatar"])

		# Verify in DB
		loved_one.refresh_from_db()
		self.assertTrue(loved_one.avatar.name.endswith("avatar.gif"))


from django.test import TransactionTestCase
import asyncio
from voice.consumers import RealtimeVoiceConsumer, VoiceChatConsumer
from accounts.models import SubscriptionTalkTimeUsage
from conversations.models import ConversationSession

class TalkTimeTrackingTests(TransactionTestCase):
	def setUp(self):
		self.user = User.objects.create_user(email="talker@example.com", password="pass12345", full_name="Talker User")
		self.plan = Plan.objects.create(name="Talk Plan", price=10.00, talk_time_limit=300)
		self.subscription = UserSubscription.objects.create(user=self.user, plan=self.plan, is_active=True)
		self.loved_one = LovedOne.objects.create(user=self.user, name="Grandpa", relationship="Grandfather")
		self.session = ConversationSession.objects.create(user=self.user, loved_one=self.loved_one, channel=ConversationSession.CHANNEL_VOICE)

	def test_end_conversation_session_creates_talk_time_usage(self):
		consumer = RealtimeVoiceConsumer()
		asyncio.run(consumer._db_end_conversation_session(self.session.id, duration_seconds=120))
		
		usage = SubscriptionTalkTimeUsage.objects.filter(subscription=self.subscription).first()
		self.assertIsNotNone(usage)
		self.assertEqual(usage.duration, 120)
		self.assertEqual(usage.session, self.session)

	def test_talk_time_limit_check(self):
		consumer = RealtimeVoiceConsumer()
		consumer.scope = {"user": self.user}
		
		# Before limit reached
		allowed, msg = asyncio.run(consumer._db_check_talk_time_limit(str(self.user.id)))
		self.assertTrue(allowed)

		# Exceed limit
		SubscriptionTalkTimeUsage.objects.create(subscription=self.subscription, session=self.session, duration=350)
		allowed, msg = asyncio.run(consumer._db_check_talk_time_limit(str(self.user.id)))
		self.assertFalse(allowed)
		self.assertIn("Talk time limit of 300 seconds reached", msg)

	def test_voice_chat_consumer_save_talk_time_usage(self):
		consumer = VoiceChatConsumer()
		consumer.user = self.user
		consumer.loved_one_id = self.loved_one.id

		asyncio.run(consumer._save_talk_time_usage(self.session.id, duration_seconds=180))

		usage = SubscriptionTalkTimeUsage.objects.filter(subscription=self.subscription).first()
		self.assertIsNotNone(usage)
		self.assertEqual(usage.duration, 180)
		self.assertEqual(usage.session, self.session)

	def test_voice_chat_consumer_check_talk_time_limit(self):
		consumer = VoiceChatConsumer()
		consumer.user = self.user
		consumer.loved_one_id = self.loved_one.id

		allowed, msg = asyncio.run(consumer._check_talk_time_limit())
		self.assertTrue(allowed)

		# Exceed limit
		SubscriptionTalkTimeUsage.objects.create(subscription=self.subscription, session=self.session, duration=300)
		allowed, msg = asyncio.run(consumer._check_talk_time_limit())
		self.assertFalse(allowed)
		self.assertIn("Talk time limit of 300 seconds reached", msg)

	def test_voice_chat_consumer_stop_stream_preserves_session_id(self):
		consumer = VoiceChatConsumer()
		consumer.user = self.user
		consumer.loved_one_id = self.loved_one.id
		consumer._current_conv_session_id = self.session.id
		consumer._current_partial_text = "Hello there"
		consumer._stream_task = None

		# Mock send
		async def mock_send(text_data):
			pass
		consumer.send = mock_send

		asyncio.run(consumer._stop_stream_handler())

		# Ensure session_id is preserved and not reset to 0
		self.assertEqual(consumer._current_conv_session_id, self.session.id)




