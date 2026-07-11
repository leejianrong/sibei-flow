# Case study: Jian's edits to "Hello / about me" (article 1)

This is the primary evidence behind the [Jian's voice](../SKILL.md#jians-voice-the-profile-for-this-projects-articles)
profile. It records the diff between the AI-drafted baseline of the first article
and Jian's hand-edits, plus the patterns extracted from it. Add new sections below
as more articles are edited, so the profile stays grounded in real data rather than
guesswork.

## The diff (baseline → Jian's edit)

```diff
-Hi, I'm Jian. I build full-stack web apps, and I'm starting this blog to write down what I'm learning while I do it.
+Hi, I'm Jian. I build full-stack web apps, and I'm starting this blog to capture what I've learnt.

-A little background first. I studied electrical and electronic engineering at Imperial College London, where I specialised in signal processing and machine learning. Not software engineering, exactly. I picked up coding after national service and fell for it properly at university, the way you do when you look up and it's suddenly 2am. These days the work is full-stack: front end, back end, and all the glue in between.
+I studied Electrical and Electronic Engineering at Imperial College London, where I specialised in signal processing and machine learning. Not software engineering, exactly. I picked up coding after national service and fell for it properly at university, the way you do when you look up and it's suddenly 2am. These days the work is full-stack: front end, back end, and machine learning applications on the side.

-So why start writing now?
-
-Because I'm learning a lot, fast, and most of it is slipping away. Some of it comes from building projects by hand, the slow way, one function at a time. A lot more of it now comes from building with agentic LLMs. I describe what I want, the agent writes a few hundred lines, and that part of the job just flies by. Which is great. It's also the problem. The code shows up faster than the understanding does.
+I decided to start writing now because I'm learning a lot in a short span of time. Some of it comes from building projects by hand, the slow way (one function at a time). A lot more of it now comes from building with agentic LLMs. I describe what I want, the agent writes a few hundred lines, and that part of the job just flies by. It's great, but the code shows up faster than the understanding does.

-This blog is where I slow that part back down. I want to catch the lessons before they scroll off the screen and write them up in a way that's short, honest, and actually useful.
-
-A quick opinion, because a blog without one is just a diary.
-
-I think LLMs and coding agents are a good thing. I love writing code by hand and I'll keep doing it for the fun of it, but the tools are genuinely excellent now, and pretending otherwise helps no one. People said the calculator would rot our maths. They said it about the TV, about search engines, about every tool that took over a chore we used to do ourselves. What actually happened is we adapted and spent the freed-up attention on harder things. I don't buy that agents will make us dumber. As a junior developer, I have a lot to learn from them, and not only about code. Watching a good model explain its reasoning has quietly made me a clearer communicator. It's better at that than a lot of engineers I've met, which is a lesson worth stealing on its own.
+This blog is where I slow down. I want to catch the lessons and write them up in a way that's short, honest, and actually useful.
+
+LLMs and coding agents are a good thing. I love writing code by hand and I'll keep doing it for the fun of it, but the tools are excellent and it's hard to pretend otherwise. People said the calculator would make kids bad at maths. They said it about the TV, about search engines, about every tool that took over a chore we used to do ourselves. What actually happened is we adapted and spent the freed-up attention on harder things. I don't buy that agents will make us dumber. As a junior developer, I have a lot to learn from them, and not only about code. Asking an LLM to explain its reasoning has been insightful, and has taught me about how to communicate clearly (which engineers are notoriously bad at).

 Who's this for?

-Mostly me. But also you, if you're a developer who feels like the ground is moving too fast. The space shifts every week. Agents tear through thousands of lines while you're still trying to hold last month's concept steady in your head. That feeling is real, and it's easy to read it as falling behind.
-
-I don't think it is. You can still learn deeply and build real experience in the age of agentic coding. The trick is being deliberate about what you slow down for. If you take one thing from this blog, let it be this: you're not obsolete, you're early.
+Mostly me. But also you, if you're a developer who feels like the ground is moving too fast. The space shifts every week, and agents tear through thousands of lines while we're trying to keep up with last month's concepts. That feeling is real, and it's easy to feel like you're falling behind.

-So try things. Build the side project. Wire up an agent and see how far it gets, then switch it off and write the next feature by hand, just because it feels good. Both count.
+I think we can still learn deeply and build real experience in the age of agentic coding. The trick is being deliberate about what we slow down for. Hopefully this blog inspires you to try things. Build the side project. Wire up an agent and see how far it gets, then switch it off and write the next feature by hand, just because it feels good. They're both valid approaches.

-When I'm not coding I'm usually at the piano, working through pop and RnB songs with a coffee going, or a beer, depending on the hour. Never both. That's the other reason I'm writing this. Some of the best ideas turn up in the gaps, away from the keyboard.
+When I'm coding I like to have a coffee going. Or a beer, but not both at the same time. When I'm not coding I'm usually at the piano, working through pop and RnB songs.

-More soon. Thanks for reading the first one.
+There'll be more coming soon. Thanks for reading the first one.

 Jian
```

## Patterns extracted

Each maps to a bullet in the [Jian's voice](../SKILL.md#jians-voice-the-profile-for-this-projects-articles)
profile.

### 1. Smooths staccato into flowing sentences (strongest signal)

Nearly every short fragment the draft used got merged into a complete sentence,
usually joined with "and" or "but":

- "Which is great. It's also the problem." → "It's great, but the code shows up faster..."
- "The space shifts every week. Agents tear through..." → "...every week, and agents tear through..."
- "Never both." → "...but not both at the same time."
- "Both count." → "They're both valid approaches."
- "More soon." → "There'll be more coming soon."
- "I don't think it is." → cut entirely

Takeaway: his rhythm is even and calm. This is why the general burstiness rule was
softened.

### 2. Deletes narrator scaffolding

Every line that announced the post's structure was removed:

- "A little background first." → gone
- "So why start writing now?" → folded into "I decided to start writing now because..."
- "A quick opinion, because a blog without one is just a diary." → gone
- "That's the other reason I'm writing this." → gone

### 3. Cuts aphorisms and motivational punchlines

- "you're not obsolete, you're early" → "Hopefully this blog inspires you to try things."
- "a lesson worth stealing on its own" → gone
- "before they scroll off the screen" → gone
- "pretending otherwise helps no one" → "it's hard to pretend otherwise"
- "the calculator would rot our maths" → "make kids bad at maths"

Kept the vivid-but-grounded language ("flies by", "tear through", "fell for it",
"2am"). The cut ones were showy or preachy, not concrete.

### 4. "We" over "you"

- "you're still trying to hold last month's concept" → "we're trying to keep up"
- "You can still learn deeply" → "I think we can still learn deeply"
- "what you slow down for" → "what we slow down for"

Kept "you" for direct address ("if you're a developer", "inspires you").

### 5. Confident on values, humble on advice

Added "I think" and "Hopefully" to predictions and takeaways, while stating the core
belief flatly ("LLMs and coding agents are a good thing"). This is the one spot the
general "don't hedge" rule bends for him.

### 6. Smaller tells

- Parentheses for asides: "(one function at a time)", "(which engineers are notoriously bad at)".
- Accuracy about his own life: "all the glue in between" → "machine learning applications on the side"; untangled the coffee/beer/piano facts (coffee or beer while coding; piano away from it).
- British spelling: "learnt", "specialised", "maths".
- Capitalises his degree: "Electrical and Electronic Engineering".

## Caveat

This is a single article. Treat the profile as strong hypotheses, not laws. Append
the next article's diff below and revise the profile where the new evidence
disagrees.
