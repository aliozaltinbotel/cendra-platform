"""GraphQL operation strings used by Brain Engine adapters.

Kept in a dedicated module so the queries can be diffed in isolation
when the upstream schema evolves and so the runtime client stays free
of inline string blobs.

Each constant is the body of a named operation; pass it to
:meth:`UnifiedDataGraphQLClient.execute` together with a variables
mapping that matches the operation signature.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CONVERSATIONS_WITH_MESSAGES_QUERY",
    "PROPERTIES_LIST_QUERY",
    "PROPERTY_DETAIL_QUERY",
    "RATE_PLANS_LIST_QUERY",
    "RATE_PLANS_WITH_CALENDAR_QUERY",
    "RESERVATIONS_LIST_QUERY",
    "REVIEWS_LIST_QUERY",
]


RESERVATIONS_LIST_QUERY: Final[str] = """\
query Reservations(
  $customerId: String!
  $orgId: String
  $providerType: ProviderType
  $propertyChannelId: String
  $limit: Int
  $skip: Int
) {
  reservations(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    propertyChannelId: $propertyChannelId
    limit: $limit
    skip: $skip
  ) {
    id
    channelEntityId
    customerChannelId
    pmsId
    transformedAt
    data {
      propertyChannelId
      propertyPmsId
      pmsId
      status
      arrivalDate
      departureDate
      createdAt
      cancellationDate
      nightsCount
      guestsCount
      amount
      currency
      otaName
      otaReservationCode
      confirmationCode
      channelBookingId
      notes
      hostNotes
      customer {
        nameSurname
        email
        phone
      }
    }
  }
}
"""


# The onboarding-api schema has no top-level ``messages`` query; message
# documents live nested inside :class:`UnifiedConversation.messages`.
# Paginating conversations and reading the embedded ``messages`` array
# in the same response keeps the archive loader to a single round-trip
# per page and aligns with the behaviour confirmed by the GraphQL owner.
CONVERSATIONS_WITH_MESSAGES_QUERY: Final[str] = """\
query ConversationsWithMessages(
  $customerId: String!
  $orgId: String
  $providerType: ProviderType
  $propertyChannelId: String
  $limit: Int
  $skip: Int
) {
  conversations(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    propertyChannelId: $propertyChannelId
    limit: $limit
    skip: $skip
  ) {
    id
    channelEntityId
    customerChannelId
    pmsId
    transformedAt
    providerType
    data {
      title
      isClosed
      messageCount
      countUnread
      lastMessageAt
      createdAt
      propertyChannelId
      reservationChannelId
      guestChannelId
      cendraGuestId
      messages {
        pmsId
        id
        messageId
        body
        sender
        createdAt
        modifiedAt
        messageType
        communicationType
        sendByAI
        aiTag
        messageSentiment
        wasHelpful
      }
    }
  }
}
"""


# Property list — mirrors Mümin's ``ListProperties`` reference query.
# Kept deliberately slim so the picker UI can page through the whole
# tenant without fetching amenity / image arrays for every row.
PROPERTIES_LIST_QUERY: Final[str] = """\
query ListProperties(
  $customerId: String!
  $orgId: String
  $providerType: ProviderType
  $limit: Int
  $skip: Int
) {
  properties(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    limit: $limit
    skip: $skip
  ) {
    id
    channelEntityId
    pmsId
    customerChannelId
    transformedAt
    data {
      title
      name
      isActive
      city
      country
      propertyType
      maxOccupancy
      bedrooms
      bathrooms
      basePrice
      baseCurrency
      pmsId
      listingId
    }
  }
}
"""


# Single-property detail — mirrors Mümin's ``GetListing`` reference
# query.  Used by the property-profile harvester to capture the full
# static knowledge (amenities, images, rooms, descriptions) that feeds
# the memory subsystem and the "what Brain knows" surface.
PROPERTY_DETAIL_QUERY: Final[str] = """\
query PropertyDetail(
  $customerId: String!
  $orgId: String!
  $providerType: ProviderType!
  $channelEntityId: String!
) {
  property(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    channelEntityId: $channelEntityId
  ) {
    id
    channelEntityId
    pmsId
    customerChannelId
    transformedAt
    data {
      title
      name
      isActive
      city
      country
      countryCode
      zipCode
      address
      street
      latitude
      longitude
      timeZone
      propertyType
      bedrooms
      bathrooms
      beds
      maxOccupancy
      areaSquareFeet
      baseCurrency
      basePrice
      cleaningFee
      petFee
      securityDepositFee
      checkInTime
      checkOutTime
      minNights
      maxNights
      instantBookable
      petsAllowed
      hasParking
      hasWifi
      wifiNetwork
      wifiPassword
      doorCode
      licenseCode
      hostName
      listingId
      customerChannelId
      pmsId
      status
      knowledgePercentage
      amenities {
        code
        name
      }
      images {
        url
        category
        sortOrder
      }
      rooms {
        id
        title
        maxOccupancy
      }
      descriptions {
        language
        typeCode
        text
      }
    }
  }
}
"""


# Rate plans (pricing / availability metadata).  The embedded
# ``calendar`` field is intentionally omitted — per-day calendar rows
# are large and belong to a dedicated restriction query rather than
# the profile harvester.
RATE_PLANS_LIST_QUERY: Final[str] = """\
query RatePlans(
  $customerId: String!
  $orgId: String
  $providerType: ProviderType
  $limit: Int
  $skip: Int
) {
  ratePlans(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    limit: $limit
    skip: $skip
  ) {
    id
    channelEntityId
    pmsId
    customerChannelId
    transformedAt
    data {
      name
      title
      propertyChannelId
      roomTypeId
      channelRatePlanId
      pmsId
      propertyPmsId
      currency
      sellMode
      rateMode
      mealType
      isActive
      parentRatePlanId
      childrenFee
      infantFee
    }
  }
}
"""


# Rate plans enriched with per-day calendar rows and occupancy options.
# Used by the property-detail UI surface (pricing / availability card)
# where the caller supplies an explicit ``from``/``to`` window.  The
# server clamps the window so a stray caller cannot request years of
# calendar data in a single round-trip.
RATE_PLANS_WITH_CALENDAR_QUERY: Final[str] = """\
query RatePlansWithCalendar(
  $customerId: String!
  $orgId: String
  $providerType: ProviderType
  $limit: Int
  $skip: Int
  $from: DateTime!
  $to: DateTime!
) {
  ratePlans(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    limit: $limit
    skip: $skip
  ) {
    id
    channelEntityId
    data {
      name
      title
      currency
      propertyChannelId
      propertyPmsId
      isActive
      rateMode
      calendar(from: $from, to: $to) {
        date
        note
        stopSell
        closeToArrival
        closeToDeparture
        minStay
        maxStay
        countAvailableUnits
        price
      }
      occupancyOptions {
        occupancy
        isPrimary
        rate
      }
    }
  }
}
"""


# Reviews — OTA / guest feedback, used both by the profile harvester
# (aggregate rating, review count) and by the memory layer to detect
# recurring complaint topics.
REVIEWS_LIST_QUERY: Final[str] = """\
query Reviews(
  $customerId: String!
  $orgId: String
  $providerType: ProviderType
  $limit: Int
  $skip: Int
) {
  reviews(
    customerId: $customerId
    orgId: $orgId
    providerType: $providerType
    limit: $limit
    skip: $skip
  ) {
    id
    channelEntityId
    pmsId
    customerChannelId
    transformedAt
    data {
      propertyChannelId
      reservationChannelId
      channelId
      otaReservationId
      pmsId
      cendraPropertyId
      cendraBookingId
      createdAt
      reviewDate
      receivedAt
      guestName
      content
      publicReview
      comment
      response
      type
      overallRating
      ota
      source
      isHidden
      isReplied
      isExpired
    }
  }
}
"""
