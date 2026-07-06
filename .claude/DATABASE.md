# StockAssist AI
## Design System
Version: 1.0

Status: Active Development

---

# Purpose

The Design System is the single source of truth for every visual element used throughout StockAssist AI.

It ensures:

• Consistency
• Scalability
• Reusability
• Accessibility
• Developer Efficiency

Every page should be built using this system.

No component should invent its own styling.

---

# Design Philosophy

StockAssist AI should look like premium financial software.

The design language should communicate:

Trust

Intelligence

Performance

Security

Professionalism

Every component should feel intentional.

---

# Design Inspiration

Primary Inspiration

Apple

Linear

Vercel

Stripe

TradingView

Bloomberg

Perplexity

Raycast

Arc Browser

Notion

---

# Visual Principles

Minimal

Premium

Readable

Spacious

Elegant

Modern

Accessible

Consistent

---

# Color System

## Brand Colors

Primary

Blue

Purpose

Primary actions

Navigation

Links

Highlights

---

Secondary

Purple

Purpose

AI

Intelligence

Analytics

Machine Learning

---

Success

Green

Purpose

Profit

Positive Changes

Completed Tasks

---

Danger

Red

Purpose

Loss

Errors

Critical Alerts

Stop Loss

---

Warning

Orange

Purpose

Medium Risk

Market Warning

Important Notice

---

Info

Cyan

Purpose

General Information

News

Reports

---

Neutral

Gray

Purpose

Borders

Text

Backgrounds

Cards

---

# Background Layers

Layer 0

Application Background

Layer 1

Page Background

Layer 2

Section Background

Layer 3

Card Background

Layer 4

Floating Components

Each layer should be visually distinct.

---

# Theme Support

Required

Light Theme

Dark Theme

Future

High Contrast Theme

---

# Glassmorphism Rules

Every glass card should have:

Soft Blur

Soft Transparency

Thin Border

Subtle Shadow

Readable Text

Never sacrifice readability.

---

# Typography

Primary Font

Inter

Future

Geist

System Font

System UI

---

# Typography Scale

Display XL

72px

Display L

60px

Display M

48px

Heading XL

40px

Heading L

32px

Heading M

28px

Heading S

24px

Title

20px

Subtitle

18px

Body

16px

Small

14px

Caption

12px

Overline

10px

---

# Font Weight

Regular

400

Medium

500

SemiBold

600

Bold

700

ExtraBold

800

---

# Line Height

Heading

120%

Body

150%

Caption

140%

---

# Spacing System

Base Unit

4px

Spacing Scale

4

8

12

16

20

24

32

40

48

56

64

72

80

96

128

Never use random spacing values.

---

# Border Radius

XS

6px

Small

10px

Medium

14px

Large

20px

XL

24px

2XL

32px

Full

999px

---

# Shadows

Small

Buttons

Cards

Medium

Dropdown

Popover

Large

Dialogs

Extra Large

Hero Cards

Glass cards should use soft shadows.

---

# Border Styles

Thin

Default

Focus

Primary

Danger

Success

Glass

Subtle White Border

---

# Component Library

Core Components

Button

Input

Card

Modal

Drawer

Dialog

Dropdown

Tooltip

Tabs

Accordion

Badge

Avatar

Navbar

Sidebar

Footer

Breadcrumb

Toast

Table

Pagination

Search

Chart

Calendar

Date Picker

Progress

Skeleton

Alert

Command Palette

---

# Buttons

Variants

Primary

Secondary

Outline

Ghost

Danger

Success

AI

Sizes

XS

Small

Medium

Large

XL

States

Default

Hover

Active

Focus

Disabled

Loading

---

# Inputs

Components

Text

Password

Search

Number

OTP

Date

Time

Select

Checkbox

Radio

Textarea

Switch

Slider

Each input includes

Validation

Label

Helper Text

Error State

Success State

---

# Cards

Card Types

Statistic Card

Stock Card

Portfolio Card

Trade Card

AI Card

News Card

Chart Card

Report Card

Notification Card

Admin Card

Every card should support

Hover

Loading

Skeleton

Responsive Layout

---

# Tables

Support

Sorting

Filtering

Searching

Pagination

Column Visibility

Sticky Header

Responsive Collapse

Export

---

# Charts

Supported Charts

Line

Area

Candlestick

Bar

Pie

Heatmap

Treemap

Gauge

Sparkline

Charts must support:

Dark Theme

Light Theme

Zoom

Tooltip

Legend

Fullscreen

Export

---

# Icons

Library

Lucide

Sizes

16

20

24

28

32

40

48

Never mix icon libraries.

---

# Avatar

Sizes

XS

S

M

L

XL

Supports

Image

Initials

Status Indicator

---

# Navigation

Desktop

Sidebar

Top Navigation

Breadcrumb

Mobile

Drawer

Bottom Navigation (Future)

---

# Motion System

Libraries

Framer Motion

GSAP

---

# Motion Tokens

Fast

150ms

Normal

300ms

Slow

500ms

Page

600ms

Hero

800ms

---

# Animation Types

Fade

Slide

Scale

Blur

Reveal

Stagger

Count Up

Parallax

Glass Glow

---

# Loading Components

Spinner

Skeleton

Progress Bar

Pulse

Chart Placeholder

Card Placeholder

---

# Empty States

Every empty state contains

Illustration

Message

Description

Primary CTA

Secondary CTA

---

# Error States

Every error state contains

Icon

Title

Description

Retry

Support Link

---

# AI Components

Special Components

Confidence Meter

Reasoning Timeline

Risk Meter

AI Badge

Model Badge

Thinking Indicator

Live Activity

Source Attribution

Recommendation Card

Debate Viewer

These components should have a distinct visual identity.

---

# Financial Components

Market Card

Stock Card

Portfolio Summary

Trade Monitor

Watchlist Item

Sector Card

Heatmap Tile

Economic Event Card

News Card

Broker Card

---

# Admin Components

Metric Card

API Status Card

AI Usage Card

User Card

Revenue Card

Log Viewer

Health Indicator

Feature Flag

Subscription Card

---

# Accessibility

Minimum WCAG AA

Keyboard Navigation

Focus Ring

Screen Reader Labels

Color Contrast

Motion Reduction Support

---

# Responsive Rules

Desktop First

Mobile Optimized

Touch Friendly

Adaptive Layouts

Responsive Typography

Responsive Tables

Responsive Charts

---

# Naming Convention

Component

StockCard.tsx

Hook

usePortfolio.ts

Service

MarketService.ts

Context

AuthContext.tsx

Provider

ThemeProvider.tsx

---

# Component Folder Structure

components/

ui/

layout/

dashboard/

market/

portfolio/

trading/

ai/

admin/

charts/

forms/

shared/

Each component should have a single responsibility.

---

# Design Review Checklist

Before any component is merged verify:

✓ Responsive

✓ Accessible

✓ Theme Support

✓ Loading State

✓ Empty State

✓ Error State

✓ Animation

✓ Reusable

✓ Type Safe

✓ Documented

---

# Long-Term Vision

The Design System should become a reusable UI framework that powers:

Web Application

Admin Portal

Mobile App

Desktop App

Future Products

Every interface should feel like it belongs to the same ecosystem.

---

# End of Design System  