from starlette.responses import HTMLResponse

from typing import Literal

from typing_extensions import Annotated, Doc


def get_scalar_html(
    *,
    openapi_url: Annotated[
        str,
        Doc(
            """
            The OpenAPI URL that Scalar should load and use.

            This is normally done automatically by FastAPI using the default URL
            `/openapi.json`.
            """
        ),
    ],
    title: Annotated[
        str,
        Doc(
            """
            The HTML `<title>` content, normally shown in the browser tab.
            """
        ),
    ],
    scalar_js_url: Annotated[
        str,
        Doc(
            """
            The URL to use to load the Scalar JavaScript.

            It is normally set to a CDN URL.
            """
        ),
    ] = "https://cdn.jsdelivr.net/npm/@scalar/api-reference",
    scalar_favicon_url: Annotated[
        str,
        Doc(
            """
            The URL of the favicon to use. It is normally shown in the browser tab.
            """
        ),
    ] = "https://fastapi.tiangolo.com/img/favicon.png",
    theme: Literal["default", "alternate", "moon", "purple", "solarized", "bluePlanet", "saturn", "kepler", "mars", "deepSpace", "laserwave", "none"] = "default"
) -> HTMLResponse:
    """
    Generate and return the HTML response that loads Scalar for the alternative
    API docs (normally served at `/scalar`).

    You would only call this function yourself if you needed to override some parts,
    for example the URLs to use to load Scalar's JavaScript and CSS.

    Read more about it in the
    [FastAPI docs for Custom Docs UI Static Assets (Self-Hosting)](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/).
    """
    
    html = f"""
    <!DOCTYPE html>
    <html>

    <head>
        <title>{title}</title>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />

        <style>
            :root {{ --scalar-custom-header-height: 50px; }}

            .custom-header {{
                height: var(--scalar-custom-header-height);
                background-color: var(--scalar-background-1);
                box-shadow: inset 0 -1px 0 var(--scalar-border-color);
                color: var(--scalar-color-1);
                font-size: var(--scalar-font-size-2);
                font-weight: bold;
                padding: 0 18px;
                position: sticky;
                justify-content: space-between;
                top: 0;
                z-index: 100;
            }}

            .custom-header,
            .custom-header .title,
            .custom-header nav {{
                display: flex;
                align-items: center;
                gap: 14px;
            }}
            .custom-header .logo-img {{ height: 30px; }}
            .custom-header a:hover {{ color: var(--scalar-color-2); }}
            .open-api-client-button {{ display: none !important; }}
        </style>
    </head>

    <body>
        <header class="custom-header scalar-app">
            <div class="title">
                <img class="logo-img" src="{scalar_favicon_url}" target="_blank">
                <b>{title}</b>
            </div>

            <nav></nav>
        </header>

        <div id="app"></div>
        
        <noscript>
            Scalar requires Javascript to function. Please enable it to browse the documentation.
        </noscript>

        <script src="{scalar_js_url}"></script>
        <script>
            Scalar.createApiReference('#app', {{
                url: '{openapi_url}',
                hideModels: true,
                documentDownloadType: 'none',
                layout: 'modern',
                searchHotKey: 'f',
                favicon: '{scalar_favicon_url}',
                theme: '{theme}'
            }});
        </script>
    </body>

    </html>
    """
    return HTMLResponse(html)
