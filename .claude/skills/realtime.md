# Real-Time Skill

## Purpose

Defines how every real-time feature should be implemented.

Always follow REALTIME_SYSTEM.md.

## Principles

Never Poll.

Always Push.

Everything must be Event Driven.

Every live module should receive updates through Socket.IO.

Redis Pub/Sub is the communication layer.

Only update affected components.

Never rerender entire pages.

## Architecture

Market

↓

Market Engine

↓

Redis Pub/Sub

↓

Event Bus

↓

Socket.IO

↓

Frontend

↓

GSAP Animation

## Used By

Dashboard

Portfolio

Scanner

Trade Monitor

Notifications

Watchlist

AI Activity

News

Morning Report
