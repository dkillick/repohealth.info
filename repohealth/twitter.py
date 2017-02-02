import re
import string


class RegexpFormatter(string.Formatter):
    """
    Fill in a .format-able string with regular expression groups.

        >>> f = RegexpFormatter()
        >>> f.format('{This} is an expansion {pattern}')
        '(.*?) is an expansion (.*?)'
        >>> print(f.fields)
        ['This', 'pattern']
        >>> f.format('We can do {custom patterns} too', special={'custom patterns': '0-9+'})
        'We can do (0-9+) too'

    """
    def __init__(self, *args, **kwargs):
        self.fields = []
        super().__init__(*args, **kwargs)

    def get_field(self, field_name, args, kwargs):
        special = kwargs.get('special', {})
        field = special.get(field_name, r'.*?')
        self.fields.append(field_name)
        return ('({})'.format(field), field_name)


class TweetPattern(object):
    """
    A pattern for tweeting.
    
    Note: Care must be taken when matching URLs in patterns - twitter shortens URLs, and
    we don't get back the full (i.e. unshortened) url from the API.

    """
    patterns = [
                'We generate a report of repository metrics for any public github repo. Try it out at {repohealth_url}',
                'If you want to find out how your favourite repository is faring, take a look at {repohealth_url}',
                ]

    @classmethod
    def all_subclasses(cls, include_self=True):
        """
        Generator that recursively yields all subclasses of this class.

        """
        if include_self:
            yield cls

        for klass in cls.__subclasses__():
            yield klass
            # Recurse into subsubclasses
            yield from klass.all_subclasses(include_self=False)

    @classmethod
    def all_patterns_all_subclasses(cls, include_self=True):
        """
        Instantiate TweetPatterns for all known subclasse patterns of this class.

        """
        patterns = []
        for klass in cls.all_subclasses(include_self=include_self):
            yield from klass.all_patterns()

    @classmethod
    def all_patterns(cls):
        """
        Instantiate a tweet pattern with the known patterns for this class.

        """
        for pattern in cls.patterns[:]:
            yield cls(pattern)

    def __init__(self, pattern):
        self.pattern = pattern

    def __repr__(self):
        return '<TweetPattern {} "{}">'.format(self.__class__.__name__, self.pattern)

    def re_match(self, tweet):
        if not hasattr(self, 'compiled_regexp'):
            def escape(pattern):
                return pattern.replace('?', r'\?').replace('*', '\*').replace('.', '\.')

            p = escape(self.pattern)
            re_formatter = RegexpFormatter()
            regexp = re_formatter.format(p)
            self.re_fields = re_formatter.fields
            self.compiled_regexp = re.compile(regexp + '$')

        return self.compiled_regexp.match(tweet)

    def condition(self, context):
        """
        Determine if this pattern is suitable for the given context.

        """
        return True

    def context(self, context):
        """
        A place for the context to be modified before it is formatted.

        """
        if self.condition(context):
            context = self.updated_context(context)
            yield (self, context)

    def updated_context(self, context):
        """
        A place for the context to be updated after the condition has been checked, but before it is used
        for formatting the tweet. This is useful for adding extra computed keys to
        the context.

        Note: Any modifications to context should be done in a copy, not the original
        input (dictionary).

        """
        return context

    def format(self, context, extra_context=None):
        """
        Where the substitution into the pattern takes place.

        """
        return self.pattern.format(**(extra_context or {}), **context)

    def drop_content(self, message, content):
        return


class NReposInCachePattern(TweetPattern):
    patterns = ['I recently generated reports for #{names[0]} and #{names[1]} on {repohealth_url}',
               ]

    def condition(self, context):
        return len(context) >= 2

    def updated_context(self, context):
        # TODO: Sort by n_stars/n_forks.
        top_repos = sorted(context.values(),
                           key=lambda k: k['github']['repo']['stargazers_count'])
        names = [content['github']['repo']['name']
                 for content in top_repos[:3]]
        return dict(**context, n_repos=len(context),
                    names=names)
        

class RepoTweet(TweetPattern):
    patterns = [
                'Just generated a health report for {name} at {url}',
                ]

    def drop_content(self, message, content):
        """
        Given this pattern was the creator of the given message, remove the appropriate
        content to prevent further tweetage about this repository.

        """
        match = self.re_match(message)
        checkable = ['uuid', 'name', 'url']

        for f, g in zip(self.re_fields, match.groups()):
            if f in checkable:
                for uuid, context in content.items():
                    if '{{{}}}'.format(f).format(**context) == g:
                        content.pop(uuid)
                        return

    def condition(self, context):
        """
        Determine if this pattern is suitable for the given context.

        """
        return True

    def context(self, full_context):
        # For each repo in the full_context.
        for uuid, context in full_context.items():
            yield from super().context(context)

    def updated_context(self, context):
        """
        A place for the context to be modified before it is formatted.

        """
        return context


def round_sig(x, sig=2):
    from math import floor, log10
    return round(x, sig-int(floor(log10(x)))-1)


def round_with_direction_string(x):
    """
    Round the given integer to the nearest 2 s.f., as well as returning a string that
    may be used to describe the rounding compared to the original (i.e. "less than", "more than", "exactly")
    """
    rounded = round_sig(x, 2)
    if rounded < x:
        over_nearly_or_exactly = 'less than'
    elif rounded > x:
        over_nearly_or_exactly = 'more than'
    else:
        over_nearly_or_exactly = 'same as'
    return rounded, over_nearly_or_exactly


# Some extremely useful statistics from the data payload.
stargazers = lambda context: context['github']['repo']['stargazers_count']
forks = lambda context: context['github']['repo']['forks_count']


class LotsOfStars(RepoTweet):
    patterns = ['Just compiled a repo report for {name} - it now has {stars_over_or_nearly}{n_stargazers} stargazers!',
                'Did you know that {name} now has {stars_over_or_nearly}{n_stargazers} stargazers on GitHub? Full report at {url}',
                ]

    def condition(self, context):
        return stargazers(context) >= 50

    def updated_context(self, context):
        context = context.copy()
        n_stars = stargazers(context)
        n_stars_rd, direction = round_with_direction_string(n_stars)
        over_nearly_or_exactly = {'less than': 'over ',
                                  'more than': 'nearly ',
                                  'same as': ''}
        
        context.update({'n_stargazers': n_stars_rd,
                        'stars_over_or_nearly': over_nearly_or_exactly[direction]})
        return context


class LotsOfForks(RepoTweet):
    patterns = ['{name} now has {forks_over_or_nearly}{forks} forks! See the full report at {url}',]

    def condition(self, context):
        return forks(context) >= 50

    def updated_context(self, context):
        context = context.copy()
        n_forks = forks(context)
        n_forks_rd, direction = round_with_direction_string(n_forks)
        over_nearly_or_exactly = {'less than': 'over ',
                                  'more than': 'nearly ',
                                  'same as': ''}
        
        context.update({'forks': n_forks_rd,
                        'forks_over_or_nearly': over_nearly_or_exactly[direction]})
        return context


def twitter_api():
    import tweepy
    import os

    consumer_key = os.environ['consumer_key']
    consumer_secret = os.environ['consumer_secret']
    access_token = os.environ['access_token']
    access_token_secret = os.environ['access_token_secret']
    
    auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
    auth.set_access_token(access_token, access_token_secret)

    api = tweepy.API(auth)
    return api


def get_tweets(api):
    public_tweets = api.user_timeline()
    for tweet in public_tweets:
        yield tweet.text


def drop_recent(recent_messages, patterns, content):
    result = patterns.copy()
    for pattern in patterns:
        for message in recent_messages:
            if pattern.re_match(message):
                pattern.drop_content(message, content)
                if pattern in result:
                    result.remove(pattern)
    return result


def tweet_status():
    patterns = TweetPattern.all_patterns_all_subclasses()

    import repohealth.generate
    avail = repohealth.generate.in_cache()

    api = twitter_api()
    recently_tweeted = list(get_tweets(api))

    # We don't need a token - the report is already generated.
    content = {uuid: repohealth.generate.repo_data(uuid, token=None)
               for uuid in avail}

    # Universally add extra content to our report context.
    for uuid, context in content.items():
        context.setdefault('uuid', uuid)
        context['name'] = context['github']['repo']['name']
        context['url'] = 'repohealth.info/report/{}'.format(uuid)

    global_context = {'repohealth_url': 'repohealth.info'}

    patterns = drop_recent(recently_tweeted, patterns, content)

    tweet_options = []
    for pattern_gen in patterns:
        for pattern, context in pattern_gen.context(content):
            tweet_options.append(pattern.format(context, global_context))

    # TODO: Filter out tweets longer than 140 NFC chars.

    if not tweet_options:
        print('Nothing to tweet :(')
    else:
        import random
        msg = random.choice(tweet_options)
        print('TWEETING: {}'.format(msg))
        if True:
            api.update_status(msg)


if __name__ == '__main__':
    tweet_status()
