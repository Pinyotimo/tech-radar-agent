**Project Name:** TechWatch

**Project Description:**

TechWatch is a personal agent that aggregates tech news, updates, and trends from GitHub, Reddit, and other connected apps to
provide users with a personalized feed of relevant information.

**Goals:**

1. **Stay informed:** TechWatch aims to keep users up-to-date on the latest tech news, trends, and updates from the world of
GitHub, open-source, and the tech industry.
2. **Personalization:** TechWatch uses natural language processing and machine learning algorithms to analyze user behavior and
preferences, providing a tailored feed of content that interests the user.
3. **Efficient information filtering:** TechWatch allows users to customize their feed by specifying topics, projects, and
keywords, ensuring that they only see the information that matters to them.

**Features:**

1. **GitHub Integration:** TechWatch aggregates new pull requests, issues, and discussions from GitHub, providing users with a
comprehensive feed of open-source updates.
2. **Reddit Integration:** TechWatch fetches new posts from Reddit, allowing users to stay informed about trending topics and
discussions.
3. **Customizable Feed:** Users can specify topics, projects, and keywords to filter their feed and receive only the
information that interests them.
4. **Notification System:** TechWatch provides customizable notifications for new updates, allowing users to stay informed even
when they're not actively checking the feed.
5. **Visualization and Dashboards:** TechWatch includes visualization and dashboard capabilities, allowing users to quickly
scan through their feed and identify key trends and insights.
6. **Machine Learning and Predictions:** TechWatch uses machine learning algorithms to analyze user behavior and preferences,
making predictions about upcoming tech trends and opportunities.

**Automation:**

- GitHub Actions generates the regular digest three times daily in Africa/Nairobi time.
- A separate alert run executes every 30 minutes and deduplicates GitHub activity, releases, security notifications,
  3%+ watchlist moves, and keyword-matching news or Reddit posts in `data/alert_state.json`.
- Configure `GROQ_API_KEY`, `GMAIL_APP_PASSWORD`, `GH_PAT`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`
  as repository secrets for unattended delivery.
- Groq uses `llama-3.1-8b-instant` by default; set `GROQ_MODEL` to another currently available Groq model if needed.
- Change alert keywords and the market threshold with `ALERT_KEYWORDS` and `ALERT_MOVE_THRESHOLD`.
- If NSE sources are temporarily unavailable, the report keeps the last stored NSE prices and labels them stale
  instead of replacing the watchlist with `N/A`.

**Key Benefits:**

1. **Stay informed:** TechWatch provides users with a comprehensive feed of tech news, updates, and trends, ensuring they stay
informed about the latest developments in the tech industry.
2. **Personalized experience:** TechWatch's machine learning and natural language processing capabilities provide a tailored
feed of content, ensuring users only see the information that matters to them.
3. **Efficient information filtering:** TechWatch's customizable feed and notification system allow users to quickly scan
through their feed and identify key trends and insights.

**Technical Requirements:**

1. **Composio Platform:** TechWatch will be built on the Composio platform, utilizing its natural language processing, machine
learning, and visualization capabilities.
2. **GitHub API:** TechWatch will integrate with the GitHub API to fetch new pull requests, issues, and discussions.
3. **Reddit API:** TechWatch will integrate with the Reddit API to fetch new posts from Reddit.
4. **Database Management:** TechWatch will use a database management system to store user preferences, feed history, and
machine learning models.

**Next Steps:**

1. **Set up Composio:** Set up the Composio platform and integrate with the GitHub and Reddit APIs.
2. **Develop Machine Learning Models:** Develop machine learning models to analyze user behavior and preferences.
3. **Design User Interface:** Design a user-friendly interface for users to customize their feed and notification settings.
4. **Test and Refine:** Test and refine the TechWatch platform, ensuring it meets user needs and expectations.
