from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field

from models.profile import (
    PersonalInfo,
    Education,
    Skills,
    Certification,
    Experience,
    Project,
    Profile,
)


class TailoredLink(BaseModel):
    """A contact/profile link in Gemini-safe list form."""

    label: str = Field(..., description="Link label from the profile, e.g. GitHub or LinkedIn.")
    url: str = Field(..., description="URL from the profile. Pass through exactly.")


class TailoredPersonalInfo(BaseModel):
    """Personal info without dict fields, so Gemini Developer API accepts the schema."""

    name: str = Field(..., description="Full name. Pass through exactly from the profile.")
    email: str = Field(..., description="Email. Pass through exactly from the profile.")
    phone: Optional[str] = Field(default=None, description="Phone. Pass through exactly from the profile.")
    links: list[TailoredLink] = Field(
        default_factory=list,
        description="Profile links as label/url objects. Use only links from the profile.",
    )

    def to_personal_info(self) -> PersonalInfo:
        return PersonalInfo(
            name=self.name,
            email=self.email,
            phone=self.phone,
            links={link.label: link.url for link in self.links},
        )


class TailoredSkillCategory(BaseModel):
    """A skill category in Gemini-safe list form."""

    category: str = Field(..., description="Skill category name from the profile.")
    skills: list[str] = Field(
        default_factory=list,
        description="Relevant skills from this category. Do not invent skills.",
    )


class TailoredSkills(BaseModel):
    """Skill categories without dict fields, so the response schema has no additionalProperties."""

    categories: list[TailoredSkillCategory] = Field(
        default_factory=list,
        description="Selected skill categories and relevant skills from the profile.",
    )

    def to_skills(self) -> Skills:
        return Skills(categories={item.category: item.skills for item in self.categories})


class TailoredExperience(BaseModel):
    """A work-experience entry selected and rewritten by the AI."""

    company: str = Field(..., description="Company name. Must match the original profile exactly.")
    role: str = Field(..., description="Job title. Must match the original profile exactly.")
    start_date: str = Field(..., description="Start date. Must match the original profile exactly.")
    end_date: str = Field(..., description="End date. Must match the original profile exactly.")
    work_mode: Optional[str] = Field(default=None, description="Work mode from the profile.")
    bullets: list[str] = Field(
        default_factory=list,
        description="Rewritten bullets based only on real profile bullets. No fabrication.",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Technologies used. Must be a subset from the original profile entry.",
    )


class TailoredProject(BaseModel):
    """A project entry selected and rewritten by the AI."""

    name: str = Field(..., description="Project name. Must match the original profile exactly.")
    description: str = Field(..., description="Truthful one-line project summary based on the profile.")
    bullets: list[str] = Field(
        default_factory=list,
        description="Rewritten bullets based only on real project content. No fabrication.",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Technologies used. Must be a subset from the original profile project.",
    )
    link: Optional[str] = Field(default=None, description="Project URL from the profile, if present.")


class TailoredProfile(BaseModel):
    """
    Gemini's structured output for a tailored resume.

    This model intentionally avoids dict fields because Gemini Developer API
    rejects JSON schemas containing additionalProperties. Conversion back to
    the app's normal Profile model happens in to_profile().
    """

    personal_info: TailoredPersonalInfo = Field(
        ...,
        description="Contact details. Pass through exactly; links must be label/url objects.",
    )
    education: list[Education] = Field(
        default_factory=list,
        description="Selected education entries from the profile, ordered by relevance.",
    )
    experience: list[TailoredExperience] = Field(
        default_factory=list,
        description="Selected work experiences, ordered by relevance, with truthful rewritten bullets.",
    )
    projects: list[TailoredProject] = Field(
        default_factory=list,
        description="Selected projects, ordered by relevance, with truthful rewritten bullets.",
    )
    skills: TailoredSkills = Field(
        default_factory=TailoredSkills,
        description="Relevant skill categories and skills from the profile only.",
    )
    certifications: list[Certification] = Field(
        default_factory=list,
        description="Selected certifications from the profile.",
    )
    achievements: str = Field(
        default="",
        description="Achievements text from the profile, optionally trimmed. Do not invent achievements.",
    )

    def to_profile(self) -> Profile:
        experiences = [
            Experience(
                company=exp.company,
                role=exp.role,
                start_date=exp.start_date,
                end_date=exp.end_date,
                work_mode=exp.work_mode,
                bullets=exp.bullets,
                list_type="bullet",
                technologies=exp.technologies,
            )
            for exp in self.experience
        ]

        projects = [
            Project(
                name=proj.name,
                description=proj.description,
                bullets=proj.bullets,
                list_type="bullet",
                technologies=proj.technologies,
                link=proj.link,
            )
            for proj in self.projects
        ]

        return Profile(
            personal_info=self.personal_info.to_personal_info(),
            education=self.education,
            experience=experiences,
            projects=projects,
            skills=self.skills.to_skills(),
            certifications=self.certifications,
            achievements=self.achievements,
        )
