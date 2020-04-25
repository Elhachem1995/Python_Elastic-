# Licensed to Elasticsearch B.V under one or more agreements.
# Elasticsearch B.V licenses this file to you under the Apache 2.0 License.
# See the LICENSE file in the project root for more information

import elasticsearch
from eland.common import ensure_es_client


class MLModel:
    """
    A machine learning model managed by Elasticsearch.
    (See https://www.elastic.co/guide/en/elasticsearch/reference/master/put-inference.html)

    These models can be created by Elastic ML, or transformed from supported python formats such as scikit-learn or
    xgboost and imported into Elasticsearch.

    The methods for this class attempt to mirror standard python classes.
    """

    TYPE_CLASSIFICATION = "classification"
    TYPE_REGRESSION = "regression"

    def __init__(self, es_client, model_id: str):
        """
        Parameters
        ----------
        es_client: Elasticsearch client argument(s)
            - elasticsearch-py parameters or
            - elasticsearch-py instance

        model_id: str
            The unique identifier of the trained inference model in Elasticsearch.
        """
        self._client = ensure_es_client(es_client)
        self._model_id = model_id

    def delete_model(self):
        """
        Delete an inference model saved in Elasticsearch

        If model doesn't exist, ignore failure.
        """
        try:
            self._client.ml.delete_trained_model(model_id=self._model_id, ignore=(404,))
        except elasticsearch.NotFoundError:
            pass
