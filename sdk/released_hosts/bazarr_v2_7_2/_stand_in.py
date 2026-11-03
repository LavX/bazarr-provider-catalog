# Stands in for two upstream imports the vendored copy does not carry.
from types import SimpleNamespace

SETTINGS_SECTIONS = ('addic7ed', 'anidb', 'animetosho', 'anticaptcha', 'assrt', 'auth', 'avistaz', 'backup', 'betaseries', 'captchaai', 'cinemaz', 'compat_endpoint', 'cors', 'deathbycaptcha', 'discover', 'embeddedsubtitles', 'emby', 'general', 'hdbits', 'jellyfin', 'jimaku', 'karagarga', 'ktuvit', 'legendasdivx', 'legendasnet', 'log', 'napiprojekt', 'napisy24', 'omdb', 'opensubtitles', 'opensubtitlescom', 'pipocas', 'plex', 'postgresql', 'proxy', 'radarr', 'seerr', 'silo', 'sonarr', 'sportarr', 'subdl', 'subf2m', 'subsarr', 'subsource', 'subsro', 'subsync', 'subx', 'titlovi', 'titulky', 'translator', 'turkcealtyaziorg', 'whisperai')
BUILT_IN_PROVIDER_MODULES = ('addic7ed', 'animekalesi', 'animesubinfo', 'animetosho', 'assrt', 'avistaz', 'betaseries', 'bsplayer', 'cinemaz', 'embeddedsubtitles', 'gestdown', 'greeksubs', 'greeksubtitles', 'hdbits', 'jimaku', 'karagarga', 'ktuvit', 'legendasdivx', 'legendasnet', 'napiprojekt', 'napisy24', 'nekur', 'opensubtitles', 'opensubtitlescom', 'pipocas', 'prijevodionline', 'regielive', 'shooter', 'soustitreseu', 'subclub', 'subdl', 'subf2m', 'subs4free', 'subs4series', 'subsarr', 'subsource', 'subsro', 'subssabbz', 'subsunacs', 'subsynchro', 'subtis', 'subtitrarinoi', 'subtitriid', 'subtitulamostv', 'subx', 'supersubtitles', 'titlovi', 'titrari', 'titulky', 'turkcealtyaziorg', 'tvsubtitles', 'whisperai', 'wizdom', 'yavkanet', 'yifysubtitles', 'zimuku')

validators = [SimpleNamespace(names=(f"{section}.enabled",)) for section in SETTINGS_SECTIONS]


class _ProviderRegistry:
    def names(self):
        return list(BUILT_IN_PROVIDER_MODULES)


provider_registry = _ProviderRegistry()
