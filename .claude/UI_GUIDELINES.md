# StockAssist AI
## UI & UX Guidelines
Version: 1.0

Status: Active Development

---

# Purpose

This document defines every UI and UX standard used throughout StockAssist AI.

Every page, component, animation, interaction, and layout must follow these guidelines.

The goal is to build a product that feels:

• Premium
• Trustworthy
• Modern
• Professional
• Calm
• Intelligent

The interface should never feel overwhelming.

---

# Design Philosophy

StockAssist AI is not a crypto exchange.

It is not a gambling application.

It is not a social media platform.

The interface should inspire confidence.

Users should immediately feel that the platform is:

Reliable

Secure

Professional

Minimal

Elegant

Fast

Every design decision should reinforce trust.

---

# Design Inspirations

Take inspiration from:

Apple

Linear

Vercel

Stripe

Notion

Perplexity

Bloomberg Terminal

TradingView

Raycast

Arc Browser

Do not copy designs.

Instead adopt their philosophy.

---

# Design Principles

Minimalism

Whitespace

Readable Typography

Consistent Layout

Meaningful Motion

Accessible Colors

Clear Hierarchy

Fast Navigation

Simple Interactions

Premium Appearance

---

# Theme Support

The platform must support:

Light Theme

Dark Theme

Every component must support both.

Neither theme should feel like an afterthought.

---

# Layout Structure

Every page follows:

Top Navigation

↓

Sidebar

↓

Header

↓

Page Content

↓

Footer (optional)

↓

Floating AI Assistant

---

# Grid System

Desktop

12 Columns

Tablet

8 Columns

Mobile

4 Columns

Cards should align perfectly.

Avoid inconsistent spacing.

---

# Responsive Breakpoints

Mobile

0–640px

Tablet

641–1024px

Laptop

1025–1440px

Desktop

1441px+

Ultra Wide

1920px+

Every page must work perfectly.

---

# Page Width

Maximum Content Width

1600px

Dashboard

Use wide layouts.

Forms

Use centered layouts.

Landing Page

Use responsive sections.

---

# Sidebar

Position

Left

Behavior

Collapsible

Pinned

Floating on Mobile

Width

Expanded

280px

Collapsed

80px

Animation

300ms

---

# Header

Contains

Search

Notifications

AI Status

Profile

Theme Switch

Broker Status

Quick Actions

Sticky

Yes

---

# Navigation

Always visible.

Never hide critical navigation.

Support:

Keyboard

Touch

Mouse

Screen Readers

---

# Cards

Cards are the foundation of the UI.

Every card should have:

Large Radius

Glass Effect

Soft Shadow

Hover Animation

Padding

Readable Typography

Rounded Corners

Large Click Area

Cards should never feel cramped.

---

# Card Sizes

Small

Statistics

Medium

Information

Large

Charts

Extra Large

AI Reports

---

# Typography

Primary Font

Inter

Fallback

System UI

Future

Geist

Font Hierarchy

Hero

64px

Page Title

40px

Section Title

30px

Card Title

22px

Body

16px

Caption

14px

Small

12px

Never use tiny unreadable text.

---

# Colors

Primary

Blue

Success

Green

Danger

Red

Warning

Orange

Information

Purple

Neutral

Gray

Colors should be soft.

Avoid oversaturated colors.

---

# Glassmorphism

Use glass cards throughout.

Rules

Blur

Soft Border

Low Transparency

Soft Shadow

Readable Text

Never overuse blur.

---

# Shadows

Small

Cards

Medium

Dropdowns

Large

Dialogs

Extra Large

Hero Sections

Use soft shadows.

Never harsh.

---

# Border Radius

Buttons

12px

Cards

24px

Dialogs

28px

Inputs

14px

Avatars

Circular

Charts

24px

Consistency is mandatory.

---

# Buttons

Primary

Solid

Secondary

Outline

Ghost

Minimal

Danger

Red

Success

Green

Loading

Spinner

Disabled

Visible

Never hide disabled state.

---

# Button Sizes

Small

Medium

Large

Extra Large

Touch-friendly.

---

# Inputs

Rounded

Clear Labels

Icons

Validation

Helper Text

Error Messages

Focus Ring

Never rely on placeholder only.

---

# Forms

Every form includes

Validation

Loading

Error

Success

Cancel

Save Draft (where applicable)

---

# Tables

Features

Sorting

Filtering

Searching

Pagination

Sticky Header

Responsive

Column Resize (future)

---

# Charts

Use consistent colors.

Support

Dark Mode

Light Mode

Interactive Tooltips

Zoom

Fullscreen

Export

---

# Animations

Animation Philosophy

Motion should guide attention.

Never distract.

---

# Libraries

GSAP

Framer Motion

---

# Animation Duration

Micro

150ms

Normal

300ms

Complex

500ms

Page Transition

600ms

---

# Scroll Animations

Cards

Fade Up

Sections

Slide In

Hero

Reveal

Charts

Grow

Numbers

Count Up

Lists

Stagger

Do not animate everything.

Animate intentionally.

---

# Hover Effects

Cards

Lift

Buttons

Glow

Icons

Scale

Charts

Highlight

Links

Underline

Subtle only.

---

# Loading States

Every page requires:

Skeleton Loader

Spinner

Progress Bar

Loading Text

Never show blank pages.

---

# Empty States

Every empty state should include:

Illustration

Explanation

Suggested Action

CTA Button

---

# Error States

Friendly Message

Retry Button

Help Link

Report Issue

Do not expose internal server errors.

---

# Notifications

Toast

Banner

Modal

Drawer

System Notification

Priority should determine style.

---

# Icons

Use Lucide Icons.

All icons should be consistent.

Avoid mixing icon libraries.

---

# Images

High Resolution

Optimized

Lazy Loaded

Responsive

Consistent Style

---

# Accessibility

Support:

Keyboard Navigation

Focus Indicators

ARIA Labels

Screen Readers

High Contrast

Color Blind Friendly

WCAG AA minimum.

---

# Performance

Lazy Loading

Image Optimization

Component Memoization

Virtual Lists

Code Splitting

Avoid Layout Shift

---

# Mobile Experience

Bottom Navigation (future)

Touch Gestures

Swipe

Responsive Cards

Floating AI Button

Optimized Charts

---

# AI Components

AI components should have a unique identity.

Characteristics

Glass Cards

Animated Status

Confidence Indicators

Reasoning Timeline

Source Attribution

AI Badge

Never present AI responses without context.

---

# Dashboard Guidelines

Dashboard should immediately answer:

What is happening?

What changed?

What should I do?

Am I at risk?

Every widget should provide value.

---

# Design Quality Checklist

Before releasing any page verify:

✓ Responsive

✓ Accessible

✓ Animated

✓ Loading State

✓ Error State

✓ Empty State

✓ Dark Theme

✓ Light Theme

✓ Consistent Spacing

✓ Correct Typography

✓ Reusable Components

✓ Performance Optimized

---

# UI Review Checklist

Every Pull Request should answer:

Does this improve readability?

Does this increase trust?

Does it match the design language?

Is it responsive?

Is it accessible?

Is animation smooth?

Can this component be reused?

If any answer is "No"

Improve it before merging.

---

# End of UI Guidelines