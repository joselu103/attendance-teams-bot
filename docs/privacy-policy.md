# Attendance CRMT Development Bot Privacy Notice

**Effective date:** 2026-08-26

## Purpose and scope

Attendance CRMT (Development) is a development-only Microsoft Teams bot used to
verify the Microsoft Teams-to-bot connectivity path. It is not a production
attendance service.

## Information processed

When a user sends a message to the bot, Microsoft Teams and Microsoft Bot Service
may send the message activity and technical metadata needed to route and respond
to that activity. This can include the message content, conversation identifiers,
user identifiers supplied by the Microsoft platform, timestamps, and request
metadata.

## Current functionality

At the effective date, the default runtime only returns a fixed connectivity response after Microsoft Bot Service authentication. When an organization explicitly enables the separately configured attendance route, the bot uses Teams SSO and an on-behalf-of exchange to call the Attendance CRMT MCP service for requester-scoped, read-only attendance data. The Attendance CRMT service remains the authority for identity mapping, authorization, audit, and data access.

The application does not intentionally persist message content, employee data, or
attendance data. Microsoft, Azure, and any organization operating Microsoft Teams
may process data under their own applicable agreements, policies, and service
configuration.

## Do not send sensitive information

Do not send attendance records, employee identifiers, credentials, access tokens,
or other sensitive or personal information to this development bot.

## Changes and contact

This notice may change as the development bot gains functionality. Questions about
this development bot or this notice should be directed to the Attendance CRMT
project maintainer.
