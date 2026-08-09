# **Non-Trivial Paper Writing Checklist**

*A section-by-section guide to writing an organized, compelling, and rigorous paper*

*Claude-generated based on Non-Trivial facilitator Angela’s Paper Writing 101 workshop [slides](https://docs.google.com/presentation/d/1Z9jpYdlXz_xlGs5NhTK2QAGN6O32trr-TOafuFftnCw/edit?slide=id.g3f5318b4e91_0_120#slide=id.g3f5318b4e91_0_120).*

## **How to use this checklist**

Work through each section as you draft. Not every sub-item will apply to every project — use the boxes as prompts to make sure you've made a deliberate choice about each element, rather than as a rigid template. Items marked (optional) or (if applicable) are exactly that. Remember: the paper limit is 8,000 words (References and Appendices are not counted). Aim for depth over breadth.

## **1\. Overall Structure**

Confirm your paper contains all non-negotiable sections before you start filling them in.

☐  Introduction

☐  Literature Review (can be woven into Introduction, a separate section, or a subsection)

☐  Methodology

☐  Results

☐  Discussion

☐  Conclusion (can be a standalone section or a subsection under Discussion)

☐  References (not included in word count)

☐  Appendices as needed (not included in word count)

## **2\. Introduction**

*Purpose: establish, justify, and contextualize your research problem and approach. Move from broad (why) to specific (how, what).*

### **2.1 Research Problem**

**Establish**

☐  Stated the long-term, global problem your research helps solve

**Justify (ITN framework)**

☐  Importance: connected the problem to institutions (e.g., UN SDGs), communities, and/or individuals

☐  Neglectedness: explained how current and proposed directions fail, or how the problem seems un-neglected but is actually misunderstood

☐  Made clear why this problem is more pressing than adjacent problems

**Contextualize**

☐  Kept the main body focused on info directly relevant to this specific project

☐  Moved tangential-but-useful background to Appendices (e.g., a primer on how CNNs work)

### **2.2 Literature Review**

**Organization**

☐  Sorted articles into 2–4 coherent categories (e.g., "using other models to solve B," "using model A to solve other problems," etc.)

**For each category, covered:**

☐  Key questions explored

☐  Key data and methods used

☐  Key results obtained

☐  Key limitations or gaps found

☐  Included only KEY results — kept it directly relevant to your project

☐  Moved deeper rabbit holes to the Appendices

**Ultimate purpose: identify research gaps**

☐  Explicitly identified the gap(s) your research targets — think "what's new?" (new model / new data / new problem / new comparison)

☐  Considered including gaps you did NOT choose to pursue (this strengthens later justification and the discussion of your research process)

### **2.3 Research Approach**

**Establish**

☐  Stated your research question clearly, specifically, and with operationalized variables

☐  Stated which gap(s) from the literature review your research targets

**Justify (ITN framework again)**

☐  Importance: explained why the status quo is unacceptable (cost / distribution / performance / other) and addressed counterarguments

☐  Neglectedness: explained how current and proposed directions fail

☐  Made clear why your approach is more promising than reasonable alternatives

**Theory of Change**

☐  Answered: who needs to do what for your research to translate to real-world impact?

☐  Framed impact in long-term, global-scale terms

☐  Included a flowchart (e.g., diagrams.net) — color-coded for multiple impact pathways if relevant

☐  Was specific and addressed likely workarounds / failure modes

## **3\. Methodology**

*Purpose: explain and justify your research methods.*

### **3.1 Dataset**

**Collection & content**

☐  Covered the Who / What / When / Why / Where / How of data collection

☐  Elaborated on the "what": features, categories, quantities, clarifications

**Justify**

☐  Stated dataset strengths (suitability for your project)

☐  Stated dataset limitations (challenges for your project)

### **3.2 Data Processing**

**Explain**

☐  How you imputed data (handled incomplete or wrong data)

☐  How you processed data (quantification, augmentations, etc.)

☐  How you selected data

**Justify**

☐  Explained why your choices are optimal versus reasonable alternatives for each step above

### **3.3 Design / Experiment**

*Structure this section however makes most sense for your project.*

**If a MODEL / PROTOTYPE (design):**

☐  Architecture

☐  Features

☐  Building process (unordered or sequential)

**If a SCIENTIFIC EXPERIMENT:**

☐  Materials

☐  Experimental procedure (likely sequential)

☐  Controls

**Guidelines to satisfy**

☐  More specific than in a conventional academic paper (your audience may not be experts in your field)

☐  Included images and "well-known" functions/formulas (loss functions, epidemiological models, etc.)

☐  Avoided or defined jargon

☐  Line-by-line code lives in an Appendix and/or a hyperlinked GitHub repo, NOT in the main body

☐  Justified each decision thoroughly — for each choice, explained why it is optimal vs. reasonable alternatives

☐  Can defend each design choice yourself (i.e., not just accepting Claude-generated code without understanding it)

## **4\. Results**

*Purpose: present and analyze your key results. Analysis can be woven into Results, live as a subsection, or (less ideal) sit in the Discussion.*

### **4.1 Quantitative Results**

**Statistical metrics reported (as applicable):**

☐  Measures of central tendency and variability

☐  Precision, recall, F1, AUROC, AUPRC

☐  Other relevant: heatmaps, graphs, R², z-score, t-score, p-value, etc.

**Presentation**

☐  Every figure/chart has a title, labeled axes, units, and a key

☐  Assumptions established up front (e.g., "all metrics rounded to X decimal places")

**Guidelines**

☐  Made figures as easy as possible for the reader to interpret

☐  If many results, sorted them into categories (by model, by task, or another sensible grouping)

☐  Included ONLY results you plan to analyze or refer to later

☐  Kept the paper focused — depth over breadth

### **4.2 Qualitative Results (if applicable)**

☐  Included qualitative results (interview quotes, field notes, observations) only where they add substance

☐  Noted that impact from qualitative results is harder to quantify — most results likely quantitative

### **4.3 Analysis**

**Observations**

☐  Discussed the extent to which results align with or challenge your expectations

☐  Called out particularly surprising outcomes

☐  Identified trends emerging in the results

**Comparisons**

☐  Compared which models / conditions performed better or worse for each measure

☐  Discussed which trends are consistent or differ between groups, and under which conditions

**Explanations**

☐  For each observation and comparison, proposed mechanisms or design choices that may have led to the result ("further research is needed" is a valid ending here)

**Confidence calibration**

☐  Discussed potential biases and how they may have influenced results

☐  Stated confidence that results are representative of reality

☐  Discussed the extent to which results generalize

## **5\. Discussion**

*Purpose: summarize and situate your research in the broader literature.*

### **5.1 Conclusion**

*Can be a standalone section OR a subsection under Discussion. Similar to an Abstract — Abstracts are often excluded in NT papers for concision.*

☐  Reiterated the research problem

☐  Reiterated the research approach

☐  Reiterated key methods

☐  Reiterated key results

☐  Kept it to \~1–2 paragraphs (a "TLDR" of the project)

### **5.2 Limitations & Future Research**

**Limitations covered (be open — this works FOR you, not against you)**

☐  Scope limitations (before execution) — e.g., data, materials

☐  Technical limitations (during execution) — e.g., compute, unexplained findings

☐  Deployment limitations (after execution) — e.g., logistics, malicious actors

☐  For each limitation, briefly explained how it affects the process or results

**Future research**

☐  Future directions follow naturally from the limitations above (scope, technical, deployment)

☐  Prioritized directions with reasoning and a rough timeline

☐  Used a numbered list for clarity

### **5.3 Recommendations & Implications**

**Implications**

☐  Explained how your research alters the landscape of the literature (contribution to new knowledge)

**Recommendations**

☐  Provided actionable steps for institutions, communities, and individuals (contribution to practical impact)

☐  Emphasized and justified each recommendation — ideally with quantitative backing

### **5.4 Cost-Benefit Analysis**

*Can be a new subsection under Discussion, combined with Recommendations, or moved to an Appendix.*

**Three-way comparison**

☐  Cost of the status quo

☐  Cost of implementing your recommendation

☐  Cost of implementing the most promising alternative

**Cost categories considered**

☐  Economic

☐  Resources

☐  Time and labor

☐  Human/animal lives and wellbeing

☐  Downstream effects (diminishing returns, exponential growth, etc.)

**For subjective measures (e.g., wellbeing), used one of:**

☐  Objective proxies (income, education, housing, community safety, access to opportunities, etc.)

☐  Established measures such as QALYs

☐  Other measures from the literature or well-reasoned custom measures

**CBA hygiene**

☐  Consistent conversion factors and baselines throughout

☐  Added all expended costs, subtracted all saved costs

☐  Justified every assumption

☐  Sorted assumptions into categories (technical estimations, monetary estimations, catchall)

☐  Detailed assumption list moved to Appendices

## **6\. References**

*Not included in word count.*

☐  Chose a proper citation format (APA, MLA, etc.) — Zoterobib recommended

☐  Consistent formatting: numbered list or hanging indents; alphabetized or not (pick one and stick with it)

☐  Inline citations follow one consistent style, e.g., (Author et al., year) OR \[citation number\]

☐  Hyperlinks added for quick access to articles

## **7\. Appendices**

*Not included in word count. Labeled Appendix A, B, C, etc.*

**Consider including:**

☐  Background knowledge and definitions (research problem, model, statistical metrics)

☐  Long lists of assumptions (general project assumptions, CBA assumptions)

☐  Code and lengthy derivations

☐  Lengthy data tables with minor results

☐  Brief deep dives on alternatives considered or interesting ideas

## **8\. Writing Principles**

### **8.1 Core Principles**

**Craft an argument WITHOUT sacrificing objectivity**

☐  Used ample statistics and literature to support claims

☐  Included well-reasoned judgments, supported by objective facts and thoughtful calibration

**Aim for accessibility WITHOUT sacrificing precision**

☐  Maintained an appropriate scientific tone

☐  Swapped dense jargon for accessible language

☐  Defined any jargon that had to be used

### **8.2 Formatting**

☐  Consistent tense throughout (past OR present)

☐  Consistent pronoun throughout (we OR I)

☐  Abbreviated recurring phrases on first mention, then used the abbreviation (diseases, models, frameworks, techniques)

☐  Added Table of Contents, hyperlinks, images, bullet points, and numbered lists where they aid the reader

☐  Considered adding an NT header and page numbers

☐  Every table and figure numbered and labeled: "Table/Figure X: Caption"

☐  Every section, subsection, and sub-subsection numbered and titled — one coherent narrative that's easy to trace

### **8.3 Productivity**

**Done \> perfect (& not done)**

☐  Got information down first — reordering and refining came later

☐  Avoided the "15 pages left to write in Week 8" scenario

**Break large tasks into small ones**

☐  Created a separate tab/doc for each section

☐  Outlined subsections/sub-subsections before writing

☐  Aimed for consistent incremental progress

## **9\. Final Pre-Submission Sweep**

☐  Under the 8,000-word limit (References and Appendices excluded)

☐  Reviewed the Demo Day Submission & Scoring tab in the Fellows' Program Guide and cross-checked the paper against every criterion

☐  Sent for feedback: facilitator, PARAs, experts, fellows

☐  Incorporated feedback and did a final read-through end to end

☐  All figures, tables, and references render correctly in the final export

☐  Hyperlinks tested and working

*You got this :D*