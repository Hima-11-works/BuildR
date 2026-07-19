from __future__ import annotations

from pydantic import BaseModel, Field
from models.tailored_profile import TailoredProfile

class AISuggestion(BaseModel):
    """An optimization recommendation implemented by the AI."""
    title: str = Field(description="Short summary of the optimization, e.g. 'Optimized React bullets'")
    explanation: str = Field(description="Factual, transparent explanation of why this was suggested based on the job description.")
    section: str = Field(description="The section this applies to, e.g., 'Experience', 'Projects', 'Skills', 'Summary'.")

class ResumeInsights(BaseModel):
    """Analysis insights about the tailored resume."""
    strengths: list[str] = Field(description="2-3 key technical strengths relative to the job requirements.")
    opportunities: list[str] = Field(description="2-3 specific opportunities for further alignment or improvement.")
    resume_length: str = Field(description="Visual evaluation of resume length, e.g., '1 Page - Optimal', '2 Pages - Slightly long'.")
    ai_confidence: str = Field(description="Confidence rating of AI tailoring, e.g. 'High', 'Medium', 'Low' based on profile-to-job matching.")

class TailoringStats(BaseModel):
    """Factual optimization statistics."""
    keywords_added: int = Field(description="Count of key skills/keywords from the job description added to the resume.")
    bullets_improved: int = Field(description="Number of work experience or project bullet points rewritten.")
    sections_reordered: int = Field(description="Number of sections improved or optimized by the AI (original section order must be preserved).")
    keywords_not_included: int = Field(description="Count of keywords from the job description that could not be added because they are not in the master resume.")

class TailoringResult(BaseModel):
    """Gemini's structured response for tailoring."""
    profile: TailoredProfile = Field(description="The structured tailored profile content.")
    suggestions: list[AISuggestion] = Field(description="List of suggestions implemented by the AI.")
    keywords_not_included_list: list[str] = Field(description="Skills from the job description that were NOT included because they are missing from the master resume.")
    stats: TailoringStats = Field(description="Factual optimization statistics.")
    insights: ResumeInsights = Field(description="Analysis quality insights.")
