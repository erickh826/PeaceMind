# PeaceMind Boon User Manual

Version: 1.0
Date: 2026-03-19
Audience: End users, internal demo users, PoC reviewers

---

## 1. What Boon Is

Boon is a mental wellness support assistant designed to:
- Listen with empathy
- Help you organize feelings
- Offer supportive reflection
- Recommend helpful learning content

Boon is not a medical tool.
Boon does not provide diagnosis or prescriptions.

---

## 2. What Languages Are Supported

Boon currently supports:
- Traditional Chinese
- Cantonese
- English

If you use other languages, Boon may stop the request and show this message:

很抱歉，阿本目前還在學習中，暫時只能用中文、粵語或英文與您交流喔！如果需要協助，請嘗試用這些語言跟我說。

Tip:
- If your message was blocked, resend in Traditional Chinese, Cantonese, or English.

---

## 3. Safety Boundaries You Should Know

Boon is built with multiple safety layers.
In normal use, this means:

- Harmful prompt manipulation attempts may be blocked
- Crisis-related phrases may trigger emergency guidance
- Unsafe medical-style outputs may be intercepted and replaced with safer support text
- Unsupported script-heavy outputs (for example Japanese kana, Hangul, Cyrillic) may be intercepted

---

## 4. Crisis Support Behavior

If your message indicates self-harm or suicide risk, Boon may skip normal chat flow and provide crisis support immediately.

Hong Kong emergency resources included in the response may include:
- Suicide Prevention Services: 2389 2222 (24 hours)
- The Samaritans hotline: 2382 0000 (24 hours)
- Hospital Authority mental health line: 18111 (24 hours)
- Emergency services: 999

If you are in immediate danger, call 999 now.

---

## 5. How to Use Chat

### 5.1 Start a conversation

1. Open the chat page
2. Type your feelings in the input box
3. Press Send (or Enter)

Input limits:
- Frontend soft cap and warning UX are enabled
- Backend hard limit is 1500 characters

### 5.2 Recommended user style

For best response quality:
- Describe your current emotion first
- Add context (what happened, when, with whom)
- Use short paragraphs

---

## 6. Session Memory and Reset

Boon includes session memory in this PoC.

What this means:
- Boon can keep context across turns in the same session
- A reset action clears the current session memory
- Reset starts a fresh conversation context

Current PoC limitations:
- In-memory storage only
- Session data may be lost after service restart or expiration

---

## 7. Courses Page and Inline Recommendations

Boon can suggest wellness videos during chat.

### 7.1 Courses page

The Courses page includes curated YouTube videos on topics such as:
- Mindfulness
- Anxiety support
- Emotion regulation
- Productivity and motivation
- Positive mindset
- Relationship awareness
- Inner-child healing

### 7.2 Inline recommendation cards in chat

During conversation, Boon may show a recommendation card when keywords are detected.
You can click the card to open a video modal without leaving the chat context.

---

## 8. Common Block or Intercept Scenarios

You may see a blocked or intercepted response when:
- Input appears to be instruction override or jailbreak behavior
- Input is in an unsupported language
- Output appears to contain unsafe medical advice
- Output appears to reveal internal system instructions

This is expected behavior by design.

---

## 9. Troubleshooting

### Issue: I got blocked but my message is harmless

Try:
- Rewrite clearly in Traditional Chinese, Cantonese, or English
- Remove command-like wording
- Send shorter, plain-language sentences

### Issue: Response looks generic or safety-focused

This can happen when safeguards are triggered.
Try rephrasing your message with emotional context instead of requesting diagnosis or medication advice.

### Issue: Conversation context was lost

Possible reasons:
- Session reset was used
- Session expired
- Service restarted (PoC in-memory store)

---

## 10. Best-Practice Usage Guidance

Do:
- Use Boon for emotional support and reflection
- Reach out to human professionals for diagnosis and treatment
- Contact emergency services for urgent risk

Do not:
- Rely on Boon for medical prescriptions
- Treat Boon as a clinical diagnosis system

---

## 11. Quick Safety Disclaimer

Boon is a psychological support assistant for PoC use.
It is not a doctor, therapist, or emergency replacement.
For urgent danger, contact local emergency services immediately.

---

## 12. Related Project Documents

- Architecture and security design: PHASE5_ARCHITECTURE.md
- Project overview and API basics: README.md
- Deployment notes: DEPLOY_VERCEL.md
- Security testing results: RED_TEAM_REPORT.md
