TEST FILES (use Phase 1 JD skills (the transcripts were written for a Python backend JD))

01_main_interview_long.vtt  (Teams style, <v Name> tags, 3 speakers, ~10k chars -> 2 chunks)
   Speakers: Neha Verma (Interviewer), Rahul Mehta (Candidate), Priya Nair (HR).
   -> Priya ko manually "Interviewer" set karo (auto-guess usse Candidate bana dega). Ye role-correct test hai.
   Expected: Python Strong, REST API design Strong, Communication Strong, Docker Adequate, SQL Adequate (window functions weak),
   System design Weak, AWS Not covered, Team leadership Not covered.
   Recommendation likely: Fit (confidence Medium, kyunki AWS/leadership probe nahi hue).
   Interview quality: leading questions ("you'd agree indexes always make queries faster, right?",
   "you've worked with PostgreSQL, right?") aane chahiye. Not covered list me AWS + Team leadership.

02_too_short_candidate.vtt  (Zoom style "Name: text")
   Expected: parse ho jayega, par Evaluate pe error: "Candidate ke jawab bahut kam hain".

03_weak_candidate_zoom_style.vtt  (Zoom style, curly apostrophes)
   Speakers: Neha Verma, Rahul Mehta -> yahan names same hain, Rahul = Candidate.
   Expected: Python Weak, SQL Weak, Communication Weak/Adequate, baaki Not covered,
   recommendation Weak fit ya Not a fit; interviewer ke leading questions ("haven't you?", "You must be good at teamwork then?").
   Curly apostrophes (’) ke saath quote verification bhi test hota hai.

04_broken_not_a_transcript.vtt
   Expected: "Transcript parse nahi hua" error.

05_single_speaker.vtt
   Expected: warning "Sirf 1 speaker mila" aur Candidate-only roles; evaluation chalega par interviewer ke bina.

EXTRA MANUAL TESTS
 - Khali transcript box + Parse -> error.
 - Skills box khali -> error.
 - Roles ulte kar do (Neha = Candidate) -> model ko galat input milega; "Candidate ke jawab kam" ya Not covered bahut aayenge.
 - .env me galat key daalo -> 401 message.
 - Evaluate ke beech internet band -> retry + network error message.
 - Result ke baad Disagree select karo bina reason ke -> error, phir reason daalke JSON report download.
