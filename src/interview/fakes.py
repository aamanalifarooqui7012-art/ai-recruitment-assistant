"""Offline stand-in for the model. Lets tests and `--fake` demo run with no API key.
Writes simple templated questions that obey the same rules the validators enforce.
(The model is only used to word questions; skills and evidence come from Phase 1 and Phase 2.)"""
from .llm import Role, T
from .prompts import slots_from_prompt
from .schemas import GeneratedQuestion, GeneratedQuestionSet


class OfflineDemoLLM:
    async def generate(self, schema: type[T], *, role: Role, instructions: str, prompt: str) -> T:
        if schema is GeneratedQuestionSet:
            slots = slots_from_prompt(prompt)
            return GeneratedQuestionSet(questions=[self._question(s) for s in slots])  # type: ignore[return-value]
        raise TypeError(f"OfflineDemoLLM cannot produce {schema}")

    @staticmethod
    def _question(slot: dict) -> GeneratedQuestion:
        skill, qtype = slot["skill_name"], slot["question_type"]
        section, bucket = slot.get("section", "resume_based"), slot.get("bucket")
        n = slot["slot_id"]
        if section != "resume_based":
            # 5 templates per section: a section has at most 5 slots, so no two slots of one section share a
            # template, even when the same skill comes up twice (otherwise the duplicate check would reject them).
            if section == "basic":
                question = (
                    f"What is {skill}, and what is it mainly used for?",
                    f"What is the difference between the two most commonly confused basic concepts in {skill}?",
                    f"Name three basic terms of {skill} and explain each in one sentence.",
                    f"What are the main data types or building blocks of {skill}, and when do you use each?",
                    f"What is a common beginner mistake in {skill}, and how do you avoid it?",
                )[n % 5]
            elif section == "scenario_case":
                question = (
                    f"A {skill}-based service slows down from 200 ms to 3 s after a release, and users complain. "
                    f"How would you find the cause and fix it?",
                    f"Two stakeholders ask for conflicting changes to a {skill} component, and the deadline is in two days. "
                    f"How would you decide and communicate?",
                    f"A {skill} job silently produced wrong data for a week before anyone noticed. "
                    f"How would you contain the damage and prevent a repeat?",
                    f"Traffic to a {skill} system grows 10x in a month and the current design starts failing. "
                    f"What would you change first, and why?",
                    f"A security review finds that a {skill} setup exposes sensitive data to every internal user. "
                    f"How would you close the gap without stopping the teams that depend on it?",
                )[n % 5]
            else:
                fence = "```"
                question = (
                    f"Here is a small {skill} snippet. What does it print, and why?\n{fence}python\n"
                    f"nums = [1, 2, 3, 4]\ntotal = 0\nfor n in nums:\n    if n % 2 == 0:\n        total += n\nprint(total)\n{fence}",
                    f"This {skill} function should return the largest value of a list, but "
                    f"largest([3, 9, 2]) returns 3. Find the bug and fix it.\n{fence}python\n"
                    f"def largest(items):\n    best = items[0]\n    for x in items:\n        if x < best:\n"
                    f"            best = x\n    return best\n{fence}",
                    f"Complete this {skill} function so that count_words('a b a') returns {{'a': 2, 'b': 1}}.\n"
                    f"{fence}python\ndef count_words(text):\n    counts = {{}}\n    for word in text.split():\n"
                    f"        # your code here\n    return counts\n{fence}",
                    f"Explain in simple words what this {skill} code does, line by line.\n{fence}python\n"
                    f"names = ['ann', 'bob', 'cy']\nresult = [s.upper() for s in names if len(s) > 2]\nprint(result)\n{fence}",
                    f"What does this {skill} code return for is_even(7), and how would you shorten it?\n"
                    f"{fence}python\ndef is_even(n):\n    if n % 2 == 0:\n        return True\n    else:\n"
                    f"        return False\n{fence}",
                )[n % 5]
            return GeneratedQuestion(
                slot_id=n, question=question, answer_keywords=[skill.lower(), "loop", "condition", "output"],
            )
        if bucket == "project_hands_on":
            proj = slot["projects"][slot["slot_id"] % len(slot["projects"])]   # two slots on one skill -> different projects
            name = proj["project_name"]
            question = {
                "technical": f"In '{name}', how did you implement the {skill} part and why did you choose that approach?",
                "scenario": f"In '{name}', suppose the {skill} component started failing under load. How would you investigate and fix it?",
                "behavioural": f"Tell me about the hardest problem you hit with {skill} in '{name}' and how you resolved it.",
            }[qtype]
        elif bucket == "listed_only":
            question = {
                "technical": f"Explain how {skill} works and when it is the right choice.",
                "scenario": f"A service needs {skill} to handle a growing workload. What would you consider first?",
                "behavioural": f"Describe a situation where choosing between {skill} and an alternative mattered.",
            }[qtype]
        else:
            # No JD sentence in the wording (two skills can share one JD line) and a different template per slot,
            # so gap questions never read as duplicates of each other.
            question = (
                f"Which fundamentals of {skill} would you explain first, and how would you begin a simple task with it?",
                f"If you had never used {skill}, what core ideas would you learn first, and how would you check that {skill} fits a small project?",
                f"Walk me through a basic {skill} exercise: what would you set up, what could go wrong, and how would you confirm it works?",
            )[slot["slot_id"] % 3]
        low = skill.lower()
        return GeneratedQuestion(
            slot_id=slot["slot_id"], question=question, answer_keywords=[low, "trade-offs", "testing", "failure handling"],
        )
