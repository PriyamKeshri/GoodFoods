# Demo video script (≈ 6 minutes)

**Setup before recording**
- `make run` (or `streamlit run streamlit_app.py`). Browser at 1440×900, zoom 100%.
- For fast replies, record with the **Groq** profile (`GROQ_API_KEY` in `.env`; same Llama 3.1 8B). With local
  Ollama, trim the waiting time in editing and say so on screen ("sped up: local 8B on a laptop").
- Click **Start new conversation** in the sidebar before each scene. Reset demo data if needed: `make reset`.
- Screen recorder: QuickTime (File → New Screen Recording) or OBS. Record the microphone.

---

### 0:00 – 0:30 · Hook
*Screen: Concierge home page. Let the rabbit wave for a second before speaking.*

> "GoodFoods has 76 restaurants across Bengaluru. Every evening, guests call or WhatsApp to book, and on a busy
> Friday many of those requests go unanswered, or end at 'sorry, we're full'. I built an AI concierge on a small
> open model, Llama 3.1 8B, that books in seconds, understands Hinglish, and never lets a guest leave just because
> one outlet is full."

### 0:30 – 1:45 · Discovery, sold-out recovery and booking
1. Click the **rabbit mascot** in the bottom-right corner. The *Popular requests* panel opens.
   > "The mascot greets guests and offers popular requests. Each one is sent as an ordinary message, so the model
   > still decides what to do with it."
2. Click **Italian dinner for two, Saturday 8 PM**.
3. Point at the result rows: Trattoria Verde, Indiranagar shows a rose **Fully booked at 8:00 PM** pill; the Lavelle
   Road and other outlets show a green **Available at 8:00 PM** pill.
   > "The Indiranagar outlet is sold out. Instead of saying no, the concierge offers a later time there and the same
   > restaurant's other outlets, which have tables at 8. Each row says why it fits."
4. Map on the right: violet pins mark the recommendations, magenta marks the sold-out outlet.
5. Click **Reserve this table** on Lavelle Road, then type: `Priya Sharma, 98450 11122`.
6. The **Table held** card appears.
   > "It holds the table for ten minutes, but nothing is booked until I approve the summary."
7. Click **Confirm reservation**. The ticket appears with the confirmation code, a WhatsApp note and
   **Add to calendar**.

### 1:45 – 2:30 · Glass box: the agent trace
*Expand **Agent trace** under the last two replies.*
> "Every step is visible. The model decided to invoke `hold_table`; this is a real MCP tool call, with its
> arguments and structured result. Nothing is routed by keywords: the model decides."

Point at a **Guardrail** line if present (for example *Resolved dates for the model*, or a repaired argument).

### 2:30 – 3:15 · Hinglish and managing a reservation
1. **Start new conversation** → click the rabbit → **Hinglish request: biryani for four**.
   > "Code-mixed Hindi: 'kal raat 8 baje', meaning tomorrow at 8 PM, for four. It resolves 'kal' correctly."
2. **Start new conversation** → type `Please move my booking GF-ANR42 to 9pm`.
   > "Changes are two-step as well. First a preview…" Click **Apply amendment**. "…then it is applied."

### 3:15 – 4:00 · Why guardrails matter (the 8B story)
1. Sidebar: switch **Reliability guardrails** off. Start a new conversation and type:
   `Book Thali Theory Jayanagar for 3 tomorrow at 1pm and confirm it`
   > "Without the reliability layer, a raw 8B model will often guess a name or phone number, or claim a booking is
   > confirmed when it isn't."
2. Switch guardrails **on** and repeat.
   > "With it on, server-declared policies stop invented contact details and premature confirmations, and a
   > verifier checks every reply against the tool results."
3. *Cut to* **Architecture → Evals**:
   > "I measured this: 17 scripted conversations, two models, guardrails on and off." Read out the headline numbers.

### 4:00 – 4:45 · Architecture: MCP
*Architecture page: the **Architecture** tab, then **MCP server**, then **Tool console**.*
> "The concierge agent is written from scratch, with no LangChain. It talks to a GoodFoods MCP server over the
> official protocol. Here are its ten tools, live from `tools/list`, including the policies the server declares, such
> as 'confirm only in a later turn'. Because it is MCP, the same server can plug into any MCP-compatible assistant or
> agent, so GoodFoods becomes bookable by AI assistants."

In the tool console, pick `hold_table` and click **Invoke via MCP**: the placeholder phone number is rejected with
corrective guidance.

### 4:45 – 5:30 · Business view
1. **Operations**: occupancy heatmap, highest occupancy this evening, channel mix, no-show rate; the *Concierge
   performance* tab (funnel, guardrail interventions); *Expansion signals*.
   > "Every 'no match' is market research: it shows where we turn guests away. That is input for the next outlet."
2. **ROI Model**: drag *Attribution confidence* down.
   > "Even with half the benefit written off, the concierge breaks even at less than half a guest per restaurant per
   > day. At 8B prices, inference costs about ₹6,000 a month."

### 5:30 – 6:00 · Close
> "To sum up: a measurable business case, a small model made reliable by design, and a protocol-native architecture
> that extends to every channel, and to salons, clinics or any business that sells time slots. The repository has
> the use case document, evals and a three-command setup. Thank you for watching."

---

**Fallback line if the model is slow on camera:** "It's an 8B model running locally on my laptop; on hosted
inference the same turn takes about two seconds."
