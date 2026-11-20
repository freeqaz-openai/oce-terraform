"""Validate the exact EKS target and credential shared by the reference commands."""

import os


def require_kuberc_opt_out():
    if os.environ.get("KUBECTL_KUBERC") != "false":
        raise ValueError(
            "Credentialed reads require the credential owner to explicitly set "
            "KUBECTL_KUBERC=false in the operator shell. Use this only if your "
            "credential policy permits running kubectl without preferences."
        )


def verify_eks_view(view, context, arn, endpoint, ca):
    """Accept the minified view of a kubeconfig from AWS CLI v2."""
    scopes = arn.split(":", 5)
    region = scopes[3]
    cluster_name = scopes[5].removeprefix("cluster/")
    try:
        [selected] = view["clusters"]
        [context_entry] = view["contexts"]
        [user_entry] = view["users"]
        user = user_entry["user"]
        token = user["exec"]
        profile = os.environ.get("AWS_DEFAULT_PROFILE") or os.environ.get("AWS_PROFILE")
        verified = (
            view["current-context"] == context_entry["name"] == context
            and selected["name"] == context_entry["context"]["cluster"] == arn
            and user_entry["name"] == context_entry["context"]["user"]
            and selected["cluster"] == {"server": endpoint, "certificate-authority-data": ca}
            and set(user) == {"exec"}
            and token["command"] == "aws"
            and token["apiVersion"] == "client.authentication.k8s.io/v1beta1"
            and token["args"] == [
                "--region", region, "eks", "get-token",
                "--cluster-name", cluster_name, "--output", "json",
            ]
            and (token.get("env") or []) in ([], [{"name": "AWS_PROFILE", "value": profile}])
            and not token.get("provideClusterInfo", False)
        )
    except (IndexError, KeyError, TypeError, ValueError):
        verified = False
    if not verified:
        raise ValueError("The kubeconfig does not match the verified EKS cluster and stock AWS token command.")
