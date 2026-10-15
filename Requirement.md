# Project Analysis of a GitHub project

## General Instructions

* This is an open project, in which you have to show that you can analyse a large code base from an open source project. This document lists a few constraints for your choice of project and gives you some directions for your analysis but in general you should show that you can analyse a project, find or make your own tools or scripts to examine the aspect of the the system’s design you are interested in, and present your findings. You are not limited to the techniques and tools covered in the lectures but they are sufficient to make an interesting analysis. The goal of the exercise is to show the breadth and the depth of your analysis in the way you communicate your results.
* The project is worth 50% of the total grade for this module.
* You need to submit a zip file containing:
  1. A PDF report of maximum 10 pages
  1. The scripts/code necessary to reproduce your analysis.
* The report should include the following sections:
  1. An introduction that describes your project and the structure and direction of your analysis (what you wanted to discover);
  2. A series of sections following the skeleton given in the core of this document: why you chose the project, stakeholders, views, etc. Each of these sections should contain your methodology and your results (how did you collect the data, how did you analyse the data, what you discovered, what answers and/or questions this analysis leads to). You should reference the sources you used to inform your analysis and show some degree of reflection about your results.
  3. A short conclusion.

## Choosing a Project

...

## Identifying Stakeholders and context view

...

## Software Metrics

As we discussed during the lectures, metrics are important to analyse a project, in particular to identify the qualities of a project - maintainability, performance, etc. Analyse your project and extract the metrics that you think are going to be relevant for the project: is your project very dynamic with lots of bug reports (velocity, i.e., how fast they are closed, is then possibly a dimension of interest)? Is the project very complex (think maintainability, lint tools)? etc. Note that you are asked to analyse your project, not just give raw numbers for the metrics. What do the metrics tell you. Why did you choose to look at these metrics? Why did you apply them to certain parts of the system or combine them in certain ways?
Once you have identified the metrics of interest for your project (and, again, you have to justify your choices), you will probably have to use tools and create scripts to run the measurements, etc.
You should consider 3-4 metrics in your analysis.